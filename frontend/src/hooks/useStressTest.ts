import { useCallback, useEffect, useRef, useState } from 'react'

type DemoLoadResponse = {
  started: boolean
  enqueued: number
  retry_after_s: number
}

export type StressTestState = {
  /** Seconds remaining before the button can be pressed again, or null when idle. */
  cooldownSeconds: number | null
  isSubmitting: boolean
  trigger: () => Promise<void>
}

/** Drives the "Stress test" button: calls POST /api/demo/load and counts
 * down the cooldown it returns, whether this request started the burst or
 * found one already in flight (DESIGN.md §9.4). */
export function useStressTest(onStarted?: () => void): StressTestState {
  const [cooldownSeconds, setCooldownSeconds] = useState<number | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const intervalRef = useRef<number | null>(null)

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
    if (isSubmitting || cooldownSeconds !== null) return
    setIsSubmitting(true)
    try {
      const response = await fetch('/api/demo/load', { method: 'POST' })
      if (!response.ok) return
      const body = (await response.json()) as DemoLoadResponse
      if (body.retry_after_s > 0) startCountdown(body.retry_after_s)
      if (body.started) onStarted?.()
    } catch {
      // A failed request just leaves the button pressable again — no
      // cooldown to show for a burst that never actually started.
    } finally {
      setIsSubmitting(false)
    }
  }, [isSubmitting, cooldownSeconds, startCountdown, onStarted])

  return { cooldownSeconds, isSubmitting, trigger }
}
