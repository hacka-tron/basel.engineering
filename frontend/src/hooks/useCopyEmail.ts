import { useEffect, useRef, useState } from 'react'
import { copyEmail, copyFeedback } from '../lib/contact'

/**
 * Copies the email address and holds what the button shows afterwards:
 * "Email copied" for 2s, or the address itself for 5s when copying failed
 * (lib/contact.ts). `result` is '' while idle. Used by the header envelope
 * (ContactReveal) and the footer "Open to work" popover (OpenToWork).
 */
export function useCopyEmail(): { result: string; copy: () => Promise<void> } {
  const [result, setResult] = useState('')
  const timerRef = useRef<number | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    }
  }, [])

  async function copy() {
    const feedback = copyFeedback(await copyEmail())
    if (!mountedRef.current) return
    setResult(feedback.text)
    if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    timerRef.current = window.setTimeout(() => setResult(''), feedback.ms)
  }

  return { result, copy }
}
