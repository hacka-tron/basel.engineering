import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  connectClusterStream,
  failureBackoffMs,
  plannedReconnectMs,
  podMapFromSnapshot,
  PLANNED_RECONNECT_MAX_MS,
  PLANNED_RECONNECT_MIN_MS,
  RECONNECT_BASE_MS,
  RECONNECT_MAX_MS,
  type ClusterPodEvent,
  type EventSourceLike,
  type ReconnectTimers,
} from './clusterStream.ts'

class FakeSource implements EventSourceLike {
  listeners = new Map<string, ((event: { data?: string }) => void)[]>()
  closed = false
  addEventListener(type: string, listener: (event: { data?: string }) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }
  close() {
    this.closed = true
  }
  emit(type: string, data?: unknown) {
    for (const listener of this.listeners.get(type) ?? []) {
      listener({ data: data === undefined ? undefined : JSON.stringify(data) })
    }
  }
}

function harness(random = () => 0.5) {
  const sources: FakeSource[] = []
  const pending: { ms: number; callback: () => void; cancelled: boolean }[] = []
  const timers: ReconnectTimers = {
    setTimeout(callback, ms) {
      const entry = { ms, callback, cancelled: false }
      pending.push(entry)
      return entry
    },
    clearTimeout(handle) {
      ;(handle as { cancelled: boolean }).cancelled = true
    },
  }
  const events: string[] = []
  const snapshots: ClusterPodEvent[][] = []
  const pods: ClusterPodEvent[] = []
  const disconnect = connectClusterStream(
    {
      onPod: (event) => pods.push(event),
      onSnapshot: (snapshot) => snapshots.push(snapshot),
      onBacklog: (event) => events.push(`backlog:${event.backlog}`),
      onUnavailable: (message) => events.push(`unavailable:${message}`),
    },
    {
      createSource: () => {
        const source = new FakeSource()
        sources.push(source)
        return source
      },
      timers,
      random,
    },
  )
  /** Fire the oldest live timer and return its delay. */
  function fireTimer(): number {
    const entry = pending.find((item) => !item.cancelled)
    assert.ok(entry, 'expected a pending reconnect timer')
    entry.cancelled = true
    entry.callback()
    return entry.ms
  }
  const livePending = () => pending.filter((item) => !item.cancelled).length
  return { sources, events, snapshots, pods, disconnect, fireTimer, livePending }
}

const pod = (name: string, ready = true, type: ClusterPodEvent['type'] = 'ADDED'): ClusterPodEvent => ({
  type,
  pod: name,
  phase: 'Running',
  ready,
})

test('failure backoff doubles from the base, caps, and keeps at least half the delay', () => {
  assert.equal(failureBackoffMs(0, () => 0), RECONNECT_BASE_MS / 2)
  assert.equal(failureBackoffMs(0, () => 1), RECONNECT_BASE_MS)
  assert.equal(failureBackoffMs(1, () => 1), RECONNECT_BASE_MS * 2)
  assert.equal(failureBackoffMs(3, () => 1), RECONNECT_BASE_MS * 8)
  assert.equal(failureBackoffMs(50, () => 1), RECONNECT_MAX_MS)
  assert.equal(failureBackoffMs(50, () => 0), RECONNECT_MAX_MS / 2)
})

test('planned reconnect waits a short random pause', () => {
  assert.equal(plannedReconnectMs(() => 0), PLANNED_RECONNECT_MIN_MS)
  assert.equal(plannedReconnectMs(() => 1), PLANNED_RECONNECT_MAX_MS)
})

test('snapshot pods are delivered together on synced, later pods one by one', () => {
  const h = harness()
  const [source] = h.sources
  source.emit('pod', pod('a'))
  source.emit('pod', pod('b', false))
  assert.equal(h.snapshots.length, 0)
  source.emit('synced', {})
  assert.deepEqual(h.snapshots, [[pod('a'), pod('b', false)]])
  source.emit('pod', pod('b', true, 'MODIFIED'))
  assert.deepEqual(h.pods, [pod('b', true, 'MODIFIED')])
  source.emit('backlog', { backlog: 4 })
  assert.deepEqual(h.events, ['backlog:4'])
})

test('an error (e.g. refused over the cap) backs off exponentially without hammering', () => {
  const h = harness(() => 1)
  const delays: number[] = []
  for (let i = 0; i < 7; i += 1) {
    h.sources.at(-1)!.emit('error')
    assert.equal(h.sources.at(-1)!.closed, true)
    assert.equal(h.livePending(), 1)
    delays.push(h.fireTimer())
  }
  assert.deepEqual(delays, [5_000, 10_000, 20_000, 40_000, 80_000, 120_000, 120_000])
  assert.equal(h.sources.length, 8)
  // Nothing was cleared while disconnected: the last view stays on screen.
  assert.equal(h.snapshots.length, 0)
  assert.equal(h.events.length, 0)
})

test('a duplicate error while a retry is pending does not stack timers', () => {
  const h = harness()
  const [source] = h.sources
  source.emit('error')
  source.emit('error')
  assert.equal(h.livePending(), 1)
})

test('a successful snapshot resets the backoff', () => {
  const h = harness(() => 1)
  h.sources.at(-1)!.emit('error')
  h.fireTimer()
  h.sources.at(-1)!.emit('error')
  assert.equal(h.fireTimer(), 10_000)
  h.sources.at(-1)!.emit('synced', {})
  h.sources.at(-1)!.emit('error')
  assert.equal(h.fireTimer(), RECONNECT_BASE_MS)
})

test('the server-requested reconnect comes back quickly and gets a fresh snapshot', () => {
  const h = harness(() => 0)
  const first = h.sources[0]
  first.emit('pod', pod('a'))
  first.emit('synced', {})
  first.emit('reconnect', {})
  assert.equal(first.closed, true)
  // The close that follows the reconnect event must not count as a failure.
  first.emit('error')
  assert.equal(h.livePending(), 1)
  assert.equal(h.fireTimer(), PLANNED_RECONNECT_MIN_MS)
  const second = h.sources[1]
  second.emit('pod', pod('b'))
  second.emit('synced', {})
  assert.deepEqual(h.snapshots, [[pod('a')], [pod('b')]])
})

test('cluster_unavailable stops for good', () => {
  const h = harness()
  h.sources[0].emit('cluster_unavailable', { message: 'no cluster' })
  assert.deepEqual(h.events, ['unavailable:no cluster'])
  assert.equal(h.sources[0].closed, true)
  h.sources[0].emit('error')
  assert.equal(h.livePending(), 0)
  assert.equal(h.sources.length, 1)
})

test('disconnect cancels a pending retry and closes the stream', () => {
  const h = harness()
  h.sources[0].emit('error')
  assert.equal(h.livePending(), 1)
  h.disconnect()
  assert.equal(h.livePending(), 0)
  assert.equal(h.sources.length, 1)
})

test('malformed frames are ignored', () => {
  const h = harness()
  const [source] = h.sources
  for (const listener of source.listeners.get('pod') ?? []) listener({ data: '{oops' })
  source.emit('synced', {})
  assert.deepEqual(h.snapshots, [[]])
})

test('podMapFromSnapshot keeps name and readiness only', () => {
  assert.deepEqual(podMapFromSnapshot([pod('a'), pod('b', false), pod('a', true, 'DELETED')]), {
    b: { name: 'b', ready: false },
  })
})
