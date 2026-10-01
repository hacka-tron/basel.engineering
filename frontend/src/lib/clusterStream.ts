export type ClusterPodEvent = {
  type: 'ADDED' | 'MODIFIED' | 'DELETED'
  pod: string
  phase: string
  ready: boolean
}

export type ClusterBacklogEvent = { backlog: number }

export type ClusterStreamCallbacks = {
  /** A change after the snapshot: apply it to the current pod set. */
  onPod?: (event: ClusterPodEvent) => void
  /**
   * The full pod set at the start of a connection. Replace the current set
   * with it, so pods that went away while disconnected disappear.
   */
  onSnapshot?: (pods: ClusterPodEvent[]) => void
  onBacklog?: (event: ClusterBacklogEvent) => void
  /** The backend has no in-cluster Kubernetes API access (e.g. local dev). */
  onUnavailable?: (message: string) => void
}

/** The parts of `EventSource` this module uses, so tests can pass a fake. */
export type EventSourceLike = {
  addEventListener: (type: string, listener: (event: { data?: string }) => void) => void
  close: () => void
}

export type ReconnectTimers = {
  setTimeout: (callback: () => void, ms: number) => unknown
  clearTimeout: (handle: unknown) => void
}

export type ClusterStreamOptions = {
  url?: string
  createSource?: (url: string) => EventSourceLike
  timers?: ReconnectTimers
  random?: () => number
}

// After a failed connection (over the server's cap, server down, network
// drop), wait RECONNECT_BASE_MS, then double each time up to RECONNECT_MAX_MS.
// A real 429/503 Retry-After is 30 s, but EventSource cannot read status codes
// or headers, so the client backs off on its own instead.
export const RECONNECT_BASE_MS = 5_000
export const RECONNECT_MAX_MS = 120_000
// The server ends each connection after its maximum lifetime with a
// `reconnect` event; come back after a short random pause so clients that were
// cut together do not all return in the same instant.
export const PLANNED_RECONNECT_MIN_MS = 500
export const PLANNED_RECONNECT_MAX_MS = 3_000

/**
 * Delay before reconnect attempt number `attempt` (0-based) after a failure:
 * exponential with "equal jitter" (half fixed, half random), so it never drops
 * below half the nominal delay and many clients spread out.
 */
export function failureBackoffMs(attempt: number, random: () => number = Math.random): number {
  const nominal = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** Math.max(0, attempt))
  return Math.round(nominal / 2 + random() * (nominal / 2))
}

export function plannedReconnectMs(random: () => number = Math.random): number {
  return Math.round(
    PLANNED_RECONNECT_MIN_MS + random() * (PLANNED_RECONNECT_MAX_MS - PLANNED_RECONNECT_MIN_MS),
  )
}

const defaultTimers: ReconnectTimers = {
  setTimeout: (callback, ms) => globalThis.setTimeout(callback, ms),
  clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof globalThis.setTimeout>),
}

function parse<T>(event: { data?: string }): T | null {
  try {
    return JSON.parse(event.data ?? '') as T
  } catch {
    return null // Ignore a malformed frame; the stream keeps going.
  }
}

/**
 * GET-only SSE (unlike /api/ask, which needs a POST body and so uses a manual
 * fetch reader — see lib/sse.ts), so the native EventSource works here.
 *
 * Reconnecting is handled here rather than by EventSource's built-in retry:
 * - Each connection starts with a snapshot (pod events, then `synced`), which
 *   replaces the pod set through `onSnapshot`.
 * - `reconnect` (the server's planned end of a connection) comes back after
 *   a short random pause.
 * - Any error (including a 429/503 when the server is at its cap, which
 *   EventSource reports as an error with no status) closes the connection and
 *   retries with exponential backoff. The last pods and backlog stay on
 *   screen meanwhile, because nothing is cleared until the next snapshot.
 * - `cluster_unavailable` (no cluster at all, e.g. local dev) stops for good.
 */
export function connectClusterStream(
  callbacks: ClusterStreamCallbacks,
  options: ClusterStreamOptions = {},
): () => void {
  const url = options.url ?? '/api/cluster/stream'
  const createSource = options.createSource ?? ((target: string) => new EventSource(target) as unknown as EventSourceLike)
  const timers = options.timers ?? defaultTimers
  const random = options.random ?? Math.random

  let source: EventSourceLike | null = null
  let timer: unknown = null
  let stopped = false
  let failures = 0

  function closeSource() {
    const current = source
    source = null
    current?.close()
  }

  function schedule(ms: number) {
    closeSource()
    if (stopped || timer !== null) return
    timer = timers.setTimeout(() => {
      timer = null
      open()
    }, ms)
  }

  function open() {
    if (stopped) return
    const current = createSource(url)
    source = current
    // Pods arriving before `synced` belong to this connection's snapshot.
    let snapshot: ClusterPodEvent[] | null = []
    const live = () => source === current

    current.addEventListener('pod', (event) => {
      if (!live()) return
      const pod = parse<ClusterPodEvent>(event)
      if (pod === null) return
      if (snapshot !== null) snapshot.push(pod)
      else callbacks.onPod?.(pod)
    })
    current.addEventListener('synced', () => {
      if (!live()) return
      failures = 0
      if (snapshot !== null) {
        callbacks.onSnapshot?.(snapshot)
        snapshot = null
      }
    })
    current.addEventListener('backlog', (event) => {
      if (!live()) return
      const backlog = parse<ClusterBacklogEvent>(event)
      if (backlog !== null) callbacks.onBacklog?.(backlog)
    })
    current.addEventListener('reconnect', () => {
      if (!live()) return
      schedule(plannedReconnectMs(random))
    })
    current.addEventListener('cluster_unavailable', (event) => {
      if (!live()) return
      const message = parse<{ message?: string }>(event)?.message ?? 'Cluster view unavailable.'
      stopped = true
      closeSource()
      callbacks.onUnavailable?.(message)
    })
    current.addEventListener('error', () => {
      if (!live()) return
      const delay = failureBackoffMs(failures, random)
      failures += 1
      schedule(delay)
    })
  }

  open()

  return () => {
    stopped = true
    if (timer !== null) timers.clearTimeout(timer)
    timer = null
    closeSource()
  }
}

/** The pod map a snapshot describes (name → readiness). */
export function podMapFromSnapshot(
  pods: ClusterPodEvent[],
): Record<string, { name: string; ready: boolean }> {
  const map: Record<string, { name: string; ready: boolean }> = {}
  for (const pod of pods) {
    if (pod.type === 'DELETED') delete map[pod.pod]
    else map[pod.pod] = { name: pod.pod, ready: pod.ready }
  }
  return map
}
