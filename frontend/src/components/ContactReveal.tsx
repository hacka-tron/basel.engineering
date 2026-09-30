import { useEffect, useRef, useState } from 'react'

const EMAIL = 'baselmabdelrahman@gmail.com'

/**
 * "Contact me" used to be a bare `mailto:` link, which immediately hands
 * control to the visitor's mail client — jarring if they don't have one
 * configured, or just want to read the address. Clicking now reveals the
 * address as plain text in place, with a copy affordance, instead — and a
 * small close button retracts it back to the "Contact me" trigger, so the
 * reveal isn't a one-way trip.
 */
function ContactReveal() {
  const [revealed, setRevealed] = useState(false)
  const [copied, setCopied] = useState(false)
  const copiedTimeoutRef = useRef<number | null>(null)

  useEffect(() => () => {
    if (copiedTimeoutRef.current !== null) window.clearTimeout(copiedTimeoutRef.current)
  }, [])

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(EMAIL)
      setCopied(true)
      if (copiedTimeoutRef.current !== null) window.clearTimeout(copiedTimeoutRef.current)
      copiedTimeoutRef.current = window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard access can be denied (permissions, insecure context); the
      // address is already visible as selectable text, so this is a
      // graceful no-op rather than an error state.
    }
  }

  if (!revealed) {
    return (
      <button
        type="button"
        onClick={() => setRevealed(true)}
        className="text-xs text-muted transition-colors hover:text-primary md:ml-4"
      >
        Contact me
      </button>
    )
  }

  return (
    <span className="flex items-center gap-2 text-xs md:ml-4">
      <span className="select-all text-primary">{EMAIL}</span>
      <button
        type="button"
        onClick={handleCopy}
        aria-label="Copy email address"
        className="rounded-[3px] border border-hairline px-2 py-1 text-muted transition-colors hover:text-primary"
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
      <button
        type="button"
        onClick={() => setRevealed(false)}
        aria-label="Hide email address"
        className="px-1 text-base leading-none text-muted transition-colors hover:text-primary"
      >
        ×
      </button>
    </span>
  )
}

export default ContactReveal
