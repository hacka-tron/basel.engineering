import { useCallback, useEffect, useRef, useState } from 'react'

type DemoLoadResponse = {
  started: boolean
  enqueued: number
  retry_after_s: number
  reason?: string
}

export type DemoCapacity = { sufficient: boolean; reason: string }

const unknownCapacity: DemoCapacity = { sufficient: false, reason: 'Checking cluster capacity…' }

async function fetchCapacity(): Promise<DemoCapacity> {
  try {
    const response = await fetch('/api/demo/capacity')
    if (!response.ok) throw new Error('capacity request failed')
    const value = await response.json()
    if (typeof value.sufficient !== 'boolean' || typeof value.reason !== 'string') throw new Error('invalid capacity')
    return value as DemoCapacity
  } catch {
    return { sufficient: false, reason: 'Cluster capacity is unavailable.' }
  }
}

export type StressTestState = {
  /** Seconds remaining before the button can be pressed again, or null when idle. */
  cooldownSeconds: number | null
  isSubmitting: boolean
  capacity: DemoCapacity
  trigger: () => Promise<void>
}

/** Drives the "Stress test" button: calls POST /api/demo/load and counts
 * down the cooldown it returns, whether this request started the burst or
 * found one already in flight (DESIGN.md §9.4). */
export function useStressTest(onVisual?: () => void): StressTestState {
  const [cooldownSeconds, setCooldownSeconds] = useState<number | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [capacity, setCapacity] = useState<DemoCapacity>(unknownCapacity)
  const intervalRef = useRef<number | null>(null)
  const busyRef = useRef(false)

  useEffect(() => {
    let mounted = true
    void fetchCapacity().then((result) => { if (mounted) setCapacity(result) })
    return () => { mounted = false }
  }, [])

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

  useEffect(() => () => {
    if (intervalRef.current !== null) window.clearInterval(intervalRef.current)
  }, [])

  const trigger = useCallback(async () => {
    if (busyRef.current || cooldownSeconds !== null) return
    busyRef.current = true
    setIsSubmitting(true)
    try {
      // Recheck at click time; the status displayed before the click may be stale.
      const currentCapacity = await fetchCapacity()
      setCapacity(currentCapacity)
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
      if (body.retry_after_s > 0) startCountdown(body.retry_after_s)
    } catch {
      setCapacity({ sufficient: false, reason: 'Cluster capacity is unavailable.' })
      onVisual?.()
      startCountdown(9)
    } finally {
      setIsSubmitting(false)
      busyRef.current = false
    }
  }, [cooldownSeconds, startCountdown, onVisual])

  return { cooldownSeconds, isSubmitting, capacity, trigger }
}
