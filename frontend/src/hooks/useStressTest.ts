import { useCallback, useEffect, useRef, useState } from 'react'

type DemoLoadResponse = {
  started: boolean
  enqueued: number
  retry_after_s: number
  reason?: string
}

export type DemoCapacity = {
  sufficient: boolean
  reason: string
  /** Set while a real burst's server-side cooldown runs: clicks are simulated until it ends. */
  realCooldown?: boolean
  /** From GET /api/demo/capacity when a real burst's cooldown lock is held. */
  retry_after_s?: number
}

const unknownCapacity: DemoCapacity = { sufficient: false, reason: 'Checking cluster capacity…' }

async function fetchCapacity(): Promise<DemoCapacity> {
  try {
    const response = await fetch('/api/demo/capacity')
    if (!response.ok) throw new Error('capacity request failed')
    const value = await response.json()
    if (typeof value.sufficient !== 'boolean' || typeof value.reason !== 'string') throw new Error('invalid capacity')
    if (value.retry_after_s !== undefined && typeof value.retry_after_s !== 'number') throw new Error('invalid capacity')
    return value as DemoCapacity
  } catch {
    return { sufficient: false, reason: 'Cluster capacity is unavailable.' }
  }
}

export type StressTestState = {
  /** Seconds remaining before the button can be pressed again, or null when idle. */
  cooldownSeconds: number | null
  /** Seconds left on a real burst's server-side cooldown, or null when none is running. */
  realCooldownSeconds: number | null
  isSubmitting: boolean
  capacity: DemoCapacity
  trigger: () => Promise<void>
}

/** Drives the "Stress test" button (DESIGN.md §9.4). With enough capacity a
 * click calls POST /api/demo/load for a real burst; otherwise — or while a
 * real burst's 5-minute server-side cooldown runs — it plays the simulated
 * version. `onReal` fires when a real burst starts so the page can show its
 * worker visualization. */
// onBegin runs synchronously when a click is accepted (before any request),
// so UI reactions to the tap (e.g. showing the diagram) happen immediately
// and can't override a choice the visitor makes while the request is pending.
export function useStressTest(onVisual?: () => void, onReal?: () => void, onBegin?: () => void): StressTestState {
  const [cooldownSeconds, setCooldownSeconds] = useState<number | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [capacity, setCapacity] = useState<DemoCapacity>(unknownCapacity)
  const [realCooldownSeconds, setRealCooldownSeconds] = useState<number | null>(null)
  const intervalRef = useRef<number | null>(null)
  const realIntervalRef = useRef<number | null>(null)
  const busyRef = useRef(false)


  const startCountdown = useCallback((seconds: number) => {
    if (intervalRef.current !== null) window.clearInterval(intervalRef.current)
    setCooldownSeconds(seconds)
    intervalRef.current = window.setInterval(() => {
      setCooldownSeconds((current) => {
        if (current === null || current <= 1) {
          if (intervalRef.current !== null) {
            window.clearInterval(intervalRef.current)
            intervalRef.current = null
          }
          return null
        }
        return current - 1
      })
    }, 1000)
  }, [])

  // A real burst holds the server's demo lock for its cooldown. Until it
  // expires, show the bunny and simulate; afterwards recheck capacity so the
  // tiger comes back only if there's still room. Counts down to a deadline
  // rather than decrementing per tick, since background tabs throttle timers.
  const startRealCooldown = useCallback((seconds: number) => {
    if (realIntervalRef.current !== null) window.clearInterval(realIntervalRef.current)
    const deadline = Date.now() + seconds * 1000
    const remaining = () => Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
    setRealCooldownSeconds(remaining())
    realIntervalRef.current = window.setInterval(() => {
      const left = remaining()
      if (left > 0) {
        setRealCooldownSeconds(left)
        return
      }
      if (realIntervalRef.current !== null) {
        window.clearInterval(realIntervalRef.current)
        realIntervalRef.current = null
      }
      setRealCooldownSeconds(null)
      void fetchCapacity().then(setCapacity)
    }, 1000)
  }, [])

  // Another visitor's real burst may hold the lock at page load or start
  // while this page sits open, so poll while no cooldown is running (and on
  // returning to the tab) rather than only checking on load and on click.
  useEffect(() => {
    if (realCooldownSeconds !== null) return
    let mounted = true
    const refresh = () => {
      if (document.visibilityState === 'hidden') return
      void fetchCapacity().then((result) => {
        if (!mounted) return
        setCapacity(result)
        if (result.retry_after_s) startRealCooldown(result.retry_after_s)
      })
    }
    refresh()
    const interval = window.setInterval(refresh, 30_000)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      mounted = false
      window.clearInterval(interval)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [realCooldownSeconds === null, startRealCooldown]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => {
    if (intervalRef.current !== null) window.clearInterval(intervalRef.current)
    if (realIntervalRef.current !== null) window.clearInterval(realIntervalRef.current)
  }, [])

  const trigger = useCallback(async () => {
    if (busyRef.current || cooldownSeconds !== null) return
    busyRef.current = true
    onBegin?.()
    setIsSubmitting(true)
    try {
      if (realCooldownSeconds !== null) {
        onVisual?.()
        startCountdown(9)
        return
      }
      // Recheck at click time; the status displayed before the click may be stale.
      const currentCapacity = await fetchCapacity()
      setCapacity(currentCapacity)
      if (currentCapacity.retry_after_s) startRealCooldown(currentCapacity.retry_after_s)
      if (!currentCapacity.sufficient) {
        onVisual?.()
        startCountdown(9)
        return
      }
      const response = await fetch('/api/demo/load', { method: 'POST' })
      if (!response.ok) throw new Error('load request failed')
      const body = (await response.json()) as DemoLoadResponse
      if (body.reason && !body.started && body.retry_after_s === 0) {
        setCapacity({ sufficient: false, reason: body.reason })
        onVisual?.()
        startCountdown(9)
        return
      }
      // Started now, or another visitor's real burst already holds the lock:
      // either way show the workers, then simulate until the cooldown ends.
      if (body.started) onReal?.()
      else onVisual?.()
      if (body.retry_after_s > 0) startRealCooldown(body.retry_after_s)
      startCountdown(9)
    } catch {
      setCapacity({ sufficient: false, reason: 'Cluster capacity is unavailable.' })
      onVisual?.()
      startCountdown(9)
    } finally {
      setIsSubmitting(false)
      busyRef.current = false
    }
  }, [cooldownSeconds, realCooldownSeconds, startCountdown, startRealCooldown, onVisual, onReal, onBegin])

  const shownCapacity: DemoCapacity = realCooldownSeconds !== null
    ? { sufficient: false, realCooldown: true, reason: 'A real stress test just ran.' }
    : capacity
  return { cooldownSeconds, realCooldownSeconds, isSubmitting, capacity: shownCapacity, trigger }
}
