import { useCopyEmail } from '../hooks/useCopyEmail'

/** Sized like the GitHub mark beside it (24px, 28px from sm); the tight viewBox makes the envelope fill it optically. */
function EnvelopeIcon() {
  return (
    <svg viewBox="2 2 20 20" className="size-6 sm:size-7 md:size-6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m3.5 6.5 8.5 6.5 8.5-6.5" />
    </svg>
  )
}

/**
 * Contact: an envelope button (aria-label "Copy email") directly left of the
 * GitHub icon, at every width. Click/tap copies the address and a toast under
 * the button says "Email copied" (announced via aria-live). If both the
 * Clipboard API and the execCommand fallback fail, the toast shows the address
 * itself for 5s so the visitor can still read it (lib/contact.ts). Hover (on
 * devices that can hover) or keyboard focus shows a "Copy email" tooltip in
 * the same place. The bubble is out of flow, so the header row never changes
 * width.
 */
function ContactReveal() {
  const { result, copy } = useCopyEmail()

  return (
    <button
      type="button"
      onClick={() => { void copy() }}
      aria-label="Copy email"
      className={`group relative flex size-11 shrink-0 items-center justify-center transition-colors hover:text-primary md:size-auto ${result ? 'text-primary' : 'text-muted'}`}
    >
      <EnvelopeIcon />
      {/* Tooltip while idle (hover/focus), the result after a click. */}
      <span
        aria-hidden="true"
        className={`pointer-events-none absolute right-0 top-full z-20 mt-1 whitespace-nowrap rounded-[3px] border border-hairline bg-panel px-2 py-1 text-xs font-normal text-primary shadow-lg ${
          result ? 'block' : 'hidden [@media(hover:hover)]:group-hover:block group-focus-visible:block'
        }`}
      >
        {result || 'Copy email'}
      </span>
      <span aria-live="polite" className="sr-only">{result}</span>
    </button>
  )
}

export default ContactReveal
