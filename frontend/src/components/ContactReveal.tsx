import { useEffect, useRef, useState } from 'react'

// Lives in the JS bundle only (never in the static HTML), so a naive scraper
// of index.html doesn't see it.
const EMAIL = 'baselmabdelrahman@gmail.com'

type Status = 'idle' | 'copied' | 'shown'

/** Legacy copy path for when the async Clipboard API is missing or denied. */
function legacyCopy(text: string): boolean {
  const previouslyFocused = document.activeElement as HTMLElement | null
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.top = '0'
  textarea.style.left = '0'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  textarea.setSelectionRange(0, text.length)
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    document.body.removeChild(textarea)
    // Selecting the textarea stole focus; hand it back (keyboard users).
    previouslyFocused?.focus?.()
  }
}

/**
 * "Contact me" is a single button: click/tap copies the email address and
 * flashes "Copied!" (announced via aria-live). If both the Clipboard API and
 * the execCommand fallback fail, the address is shown briefly instead so the
 * visitor can still read it.
 */
function ContactReveal() {
  const [status, setStatus] = useState<Status>('idle')
  const timeoutRef = useRef<number | null>(null)

  useEffect(() => () => {
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current)
  }, [])

  async function handleClick() {
    let ok = false
    try {
      await navigator.clipboard.writeText(EMAIL)
      ok = true
    } catch {
      ok = legacyCopy(EMAIL)
    }
    setStatus(ok ? 'copied' : 'shown')
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current)
    timeoutRef.current = window.setTimeout(() => setStatus('idle'), ok ? 2000 : 5000)
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      className={`inline-flex min-h-11 items-center px-2 text-sm transition-colors hover:text-primary md:min-h-0 md:px-0 ${
        status === 'idle' ? 'text-muted' : 'text-primary'
      }`}
    >
      <span aria-live="polite">
        {status === 'copied' ? 'Copied!' : status === 'shown' ? EMAIL : 'Contact me'}
      </span>
    </button>
  )
}

export default ContactReveal
