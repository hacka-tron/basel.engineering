// Detects a stalled stream: a half-open connection where `reader.read()` would
// otherwise wait forever. The server sends a `: ping` comment at least every
// 15 s (DESIGN-002 §7.4), so any received bytes, pings included, prove the
// connection is alive. Kept free of imports and DOM types so it can be unit
// tested with Node's built-in runner (`npm test`).

export type WatchdogTimers = {
  setTimeout: (callback: () => void, ms: number) => unknown
  clearTimeout: (handle: unknown) => void
}

export type IdleWatchdog = {
  /** Call whenever bytes arrive; restarts the idle countdown. */
  reset: () => void
  /** Call when the stream settles or is abandoned; the watchdog never fires after this. */
  stop: () => void
  /** Whether the idle timeout fired. */
  readonly fired: boolean
}

const defaultTimers: WatchdogTimers = {
  setTimeout: (callback, ms) => globalThis.setTimeout(callback, ms),
  clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof globalThis.setTimeout>),
}

/** Starts counting immediately; calls `onIdle` once if `timeoutMs` pass without a `reset()`. */
export function createIdleWatchdog(
  timeoutMs: number,
  onIdle: () => void,
  timers: WatchdogTimers = defaultTimers,
): IdleWatchdog {
  let handle: unknown = null
  let fired = false
  let stopped = false

  function clear() {
    if (handle !== null) timers.clearTimeout(handle)
    handle = null
  }

  function arm() {
    if (stopped || fired) return
    clear()
    handle = timers.setTimeout(() => {
      handle = null
      if (stopped) return
      fired = true
      onIdle()
    }, timeoutMs)
  }

  arm()
  return {
    reset: arm,
    stop() {
      stopped = true
      clear()
    },
    get fired() {
      return fired
    },
  }
}
