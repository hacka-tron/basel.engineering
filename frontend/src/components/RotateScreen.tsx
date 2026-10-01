import { useEffect, useId, useLayoutEffect, useRef } from 'react'
import { FULL_NAME } from '../lib/headerName'

const GITHUB_URL = 'https://github.com/hacka-tron/basel.engineering'

function isTextEntry(element: Element): boolean {
  return element instanceof HTMLTextAreaElement
    || (element instanceof HTMLInputElement && !['button', 'checkbox', 'radio', 'submit', 'reset'].includes(element.type))
    || (element instanceof HTMLElement && element.isContentEditable)
}

/**
 * Shown instead of the app while a phone is held sideways (owner, 2026-10-01:
 * the landscape layout was "basically unusable on phones"). A web page cannot
 * lock the orientation in a browser tab, so this asks the visitor to turn the
 * phone upright. App keeps itself mounted underneath (hidden and inert), so a
 * streaming answer, the conversation and the selection survive the rotation.
 */
function RotateScreen() {
  const dialogRef = useRef<HTMLDivElement>(null)
  const titleId = useId()

  // Take focus so screen readers announce the dialog, and give it back on
  // the way out. A text field is not refocused: that would pop the keyboard
  // up the moment the phone is upright again.
  useLayoutEffect(() => {
    const previous = document.activeElement
    dialogRef.current?.focus({ preventScroll: true })
    return () => {
      if (!(previous instanceof HTMLElement) || previous === document.body || isTextEntry(previous)) return
      // After the commit that removes `inert` from the app.
      requestAnimationFrame(() => {
        if (previous.isConnected && (document.activeElement === document.body || document.activeElement === null)) {
          previous.focus({ preventScroll: true })
        }
      })
    }
  }, [])

  // Escape must not reach the hidden app (it would deselect a component or
  // leave the Diagram view): stop it before the document's listeners.
  useEffect(() => {
    function stopEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') event.stopPropagation()
    }
    window.addEventListener('keydown', stopEscape, true)
    return () => window.removeEventListener('keydown', stopEscape, true)
  }, [])

  return (
    <div
      ref={dialogRef}
      role="alertdialog"
      aria-modal="true"
      aria-labelledby={titleId}
      tabIndex={-1}
      className="fixed inset-0 z-[100] flex h-dvh flex-col items-center justify-center gap-4 bg-canvas px-6 text-center font-mono text-primary outline-none"
    >
      <svg viewBox="0 0 24 24" className="rotate-upright size-12 text-cyan" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="6.5" y="2" width="11" height="20" rx="2" />
        <path d="M10.5 18.5h3" />
      </svg>
      <h1 id={titleId} className="max-w-[32ch] text-[13px] leading-[1.6]">
        Turn your phone upright to use basel.engineering
      </h1>
      <a
        href={GITHUB_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex min-h-11 items-center gap-2 px-2 text-xs text-muted transition-colors hover:text-primary"
      >
        {FULL_NAME}
        <span aria-hidden="true">·</span>
        <svg viewBox="0 0 16 16" className="size-4" fill="currentColor" aria-hidden="true">
          <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
        </svg>
        Source on GitHub
      </a>
    </div>
  )
}

export default RotateScreen
