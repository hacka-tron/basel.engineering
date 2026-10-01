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

function EnvelopeIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m3.5 6.5 8.5 6.5 8.5-6.5" />
    </svg>
  )
}

const TEXT_CLASS = 'inline-flex min-h-11 items-center px-2 text-sm transition-colors hover:text-primary md:min-h-0 md:px-0'
const ICON_CLASS = 'relative flex size-11 shrink-0 items-center justify-center transition-colors hover:text-primary'

/** Width of the "Contact me" label, reserved for the longer "Email copied". */
function TextLabel({ label }: { label: string }) {
  return (
    <span className="grid justify-items-end">
      <span aria-hidden="true" className="invisible col-start-1 row-start-1">Email copied</span>
      <span aria-live="polite" className="col-start-1 row-start-1">{label}</span>
    </span>
  )
}

/**
 * A small note under the button, right-aligned to it, used below md so the
 * result (and on clipboard failure the address itself) never changes the
 * header row's width. Out of flow, so the row cannot wrap.
 */
function Toast({ text }: { text: string }) {
  return (
    <span
      aria-live="polite"
      className={`pointer-events-none absolute right-0 top-full z-20 mt-1 whitespace-nowrap rounded-[3px] border border-hairline bg-panel px-2 py-1 text-xs text-primary shadow-lg ${text ? '' : 'sr-only'}`}
    >
      {text}
    </span>
  )
}

/**
 * "Contact me" is a single button: click/tap copies the email address and
 * flashes "Email copied" (announced via aria-live). If both the Clipboard API and
 * the execCommand fallback fail, the address is shown briefly instead so the
 * visitor can still read it.
 *
 * - `text` (md+): "Contact me"; the result replaces the label inline.
 * - `icon` (below md): an envelope, aria-label "Copy email"; "Email copied",
 *   or the address when copying fails, shows in a toast under the button, so
 *   the header row never changes width.
 */
function ContactReveal({ variant = 'text' }: { variant?: 'text' | 'icon' }) {
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

  const label = status === 'copied' ? 'Email copied' : status === 'shown' ? EMAIL : 'Contact me'
  const colour = status === 'idle' ? 'text-muted' : 'text-primary'

  if (variant === 'icon') {
    return (
      <button type="button" onClick={handleClick} aria-label="Copy email" className={`${ICON_CLASS} ${colour}`}>
        <EnvelopeIcon />
        <Toast text={status === 'idle' ? '' : label} />
      </button>
    )
  }

  return (
    <button type="button" onClick={handleClick} className={`${TEXT_CLASS} ${colour}`}>
      {/* "Email copied" sits in the width reserved for the longer of the two
          labels, right-aligned, so the header row never shifts (the GitHub
          icon stays put beside it). */}
      <TextLabel label={label} />
    </button>
  )
}

export default ContactReveal
