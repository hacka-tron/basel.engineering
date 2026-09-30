import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createIdleWatchdog, type WatchdogTimers } from './idleWatchdog.ts'

// A manual clock: timers fire only when the test advances time.
function fakeTimers() {
  let now = 0
  let nextId = 1
  const pending = new Map<number, { at: number; callback: () => void }>()
  const timers: WatchdogTimers = {
    setTimeout(callback, ms) {
      const id = nextId++
      pending.set(id, { at: now + ms, callback })
      return id
    },
    clearTimeout(handle) {
      pending.delete(handle as number)
    },
  }
  function advance(ms: number) {
    now += ms
    for (const [id, timer] of [...pending].sort((a, b) => a[1].at - b[1].at)) {
      if (timer.at <= now && pending.has(id)) {
        pending.delete(id)
        timer.callback()
      }
    }
  }
  return { timers, advance, pendingCount: () => pending.size }
}

test('fires once after the timeout with no bytes', () => {
  const clock = fakeTimers()
  let idle = 0
  const watchdog = createIdleWatchdog(45_000, () => { idle += 1 }, clock.timers)
  clock.advance(44_999)
  assert.equal(idle, 0)
  assert.equal(watchdog.fired, false)
  clock.advance(1)
  assert.equal(idle, 1)
  assert.equal(watchdog.fired, true)
  clock.advance(100_000)
  assert.equal(idle, 1)
})

test('bytes (pings included) keep resetting the countdown', () => {
  const clock = fakeTimers()
  let idle = 0
  const watchdog = createIdleWatchdog(45_000, () => { idle += 1 }, clock.timers)
  // A ping every 15 s keeps a quiet stream alive indefinitely.
  for (let elapsed = 0; elapsed < 10 * 60_000; elapsed += 15_000) {
    clock.advance(15_000)
    watchdog.reset()
  }
  assert.equal(idle, 0)
  assert.equal(clock.pendingCount(), 1)
  // Then the server goes silent.
  clock.advance(45_000)
  assert.equal(idle, 1)
})

test('never fires after stop, and reset after stop does not re-arm', () => {
  const clock = fakeTimers()
  let idle = 0
  const watchdog = createIdleWatchdog(45_000, () => { idle += 1 }, clock.timers)
  clock.advance(30_000)
  watchdog.stop()
  watchdog.reset()
  clock.advance(100_000)
  assert.equal(idle, 0)
  assert.equal(clock.pendingCount(), 0)
})

test('reset after firing does not fire again', () => {
  const clock = fakeTimers()
  let idle = 0
  const watchdog = createIdleWatchdog(1_000, () => { idle += 1 }, clock.timers)
  clock.advance(1_000)
  watchdog.reset()
  clock.advance(5_000)
  assert.equal(idle, 1)
})
