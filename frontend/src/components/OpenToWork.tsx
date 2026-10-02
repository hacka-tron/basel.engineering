import { useEffect, useId, useRef, useState } from 'react'
import { useCopyEmail } from '../hooks/useCopyEmail'
import { COPIED_TEXT } from '../lib/contact'
import { closesOnFocusOut, closesOnPointerDown, takesEscape } from '../lib/popover'

// Owner-approved wording (spec 2026-10-02 §4). OpenToWork.test.ts pins it.
const LABEL = 'Open to work'
const HEADLINE = 'Open to full-time work and freelancing'
const BODY = 'Talking to teams about full-time roles and taking on freelance projects. Copy my email and say hi.'
const COPY_LABEL = 'Copy email'
const PORTFOLIO_LINK = 'See portfolio →'

/** Green "available" dot with a soft ping (none with reduced motion). */
function StatusDot() {
  return (
    <span aria-hidden="true" className="relative flex size-2 shrink-0">
      <span className="absolute inline-flex size-full animate-ping rounded-full bg-hit opacity-50 motion-reduce:hidden" />
      <span className="relative inline-flex size-2 rounded-full bg-hit" />
    </span>
  )
}

/** Copies the email; its label turns into the result, like the header envelope's toast. */
function CopyEmailButton() {
  const { result, copy } = useCopyEmail()
  return (
    <button
      type="button"
      onClick={() => { void copy() }}
      className="inline-flex min-h-11 max-w-full items-center gap-2 rounded-[3px] border border-cyan/60 px-3 text-left text-xs text-cyan outline-none transition-colors hover:border-cyan focus-visible:ring-1 focus-visible:ring-cyan md:min-h-0 md:py-1.5"
    >
      <span aria-hidden="true">{result === COPIED_TEXT ? '✓' : '@'}</span>
      <span className="min-w-0 break-all">{result || COPY_LABEL}</span>
      <span aria-live="polite" className="sr-only">{result}</span>
    </button>
  )
}

/**
 * Footer "Open to work" item: a green dot plus label that opens a small
 * non-modal popover above the footer with one Copy email button.
 *
 * - Widths: label from 360px, dot only below (accessible name unchanged),
 *   left out below 300px, where the header envelope still copies the email.
 *   The trigger has no side padding: the label is wider than 44px, and
 *   `min-w-11` keeps a 44px target in dot-only mode.
 * - The popover is positioned against the <footer> (StatsBar makes it
 *   `relative`), right-aligned with the footer's own padding, so it spans
 *   the screen at 280px instead of hanging off the trigger.
 * - Closes on a second tap on the trigger, a pointerdown outside, focus
 *   moving to another control outside, or Escape (focus back to the
 *   trigger). Rules in lib/popover.ts; Escape order in lib/escapeKey.ts.
 * - It stays mounted (`hidden` while closed) so `aria-controls` always
 *   points at an element.
 * - "See portfolio →" (spec §5.7) closes it and hands focus to the portfolio.
 */
function OpenToWork({ onSeePortfolio }: {
  /** Selects the Portfolio topic and, on phones, opens the Portfolio view (spec §5.7). Omitted, the link is not shown. */
  onSeePortfolio?: () => void
}) {
  const [open, setOpen] = useState(false)
  const popoverId = useId()
  const headlineId = useId()
  const itemRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    function onPointerDown(event: PointerEvent) {
      if (closesOnPointerDown(event.target, itemRef.current)) setOpen(false)
    }
    // Window capture phase, after the footer tooltips' listeners (which
    // register at mount), before the diagram's document listeners.
    function onKeyDown(event: KeyboardEvent) {
      if (!takesEscape(event)) return
      event.preventDefault()
      setOpen(false)
      triggerRef.current?.focus()
    }
    document.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('keydown', onKeyDown, true)
    }
  }, [open])

  return (
    <div
      ref={itemRef}
      className="contents max-[299px]:hidden"
      onBlur={(event) => {
        if (closesOnFocusOut(event.relatedTarget, itemRef.current)) setOpen(false)
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        aria-label={LABEL}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={popoverId}
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-11 min-w-11 shrink-0 cursor-pointer touch-manipulation items-center justify-center gap-1.5 whitespace-nowrap text-muted outline-none transition-colors hover:text-primary focus-visible:ring-1 focus-visible:ring-cyan aria-expanded:text-primary md:min-h-0"
      >
        <StatusDot />
        <span className="max-[359px]:hidden">{LABEL}</span>
      </button>
      <div
        id={popoverId}
        role="dialog"
        aria-labelledby={headlineId}
        hidden={!open}
        className="absolute bottom-full right-[max(1rem,env(safe-area-inset-right))] z-30 mb-1 w-72 max-w-[calc(100vw-2rem)] whitespace-normal rounded-[3px] border border-hairline bg-panel p-3 text-left font-normal shadow-lg sm:right-4 md:right-8"
      >
        <p id={headlineId} className="flex items-center gap-2 text-xs font-medium text-primary">
          <StatusDot />
          {HEADLINE}
        </p>
        <p className="mt-1.5 text-xs leading-relaxed text-muted">{BODY}</p>
        <div className="mt-2 flex">
          <CopyEmailButton />
        </div>
        {onSeePortfolio && (
          <button
            type="button"
            onClick={() => {
              // Close without returning focus to the trigger: focus moves to the portfolio instead.
              setOpen(false)
              onSeePortfolio()
            }}
            className="mt-1 inline-flex min-h-11 items-center text-xs text-primary underline underline-offset-4 outline-none transition-colors hover:text-cyan focus-visible:ring-1 focus-visible:ring-cyan md:mt-2 md:min-h-0"
          >
            {PORTFOLIO_LINK}
          </button>
        )}
      </div>
    </div>
  )
}

export default OpenToWork
