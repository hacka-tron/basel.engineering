// The pull-up details sheet shared by the portfolio panel (desktop and
// phones) and the phone diagram (spec 2026-10-02 §5.5). It covers the bottom
// 80% of its region (lib/detailsSheet.ts SHEET_COVER); the region must be
// `relative overflow-hidden`. The desktop diagram inspector does not use it.
import { useId, type ReactNode, type Ref } from 'react'

/** Sheet reading text: 15px on phones rising to 16px at 1280px, 1.6 line-height, at most 70 characters a line. */
export const SHEET_BODY = 'max-w-[70ch] text-[clamp(0.9375rem,0.9116rem+0.1105vw,1rem)] leading-[1.6]'
/** Sheet title: 18px on phones rising to 24px. */
export const SHEET_TITLE = 'min-w-0 break-words text-[clamp(1.125rem,0.95rem+0.75vw,1.5rem)] font-semibold leading-tight tracking-tight text-primary'

export function Chevron({ direction }: { direction: 'up' | 'down' }) {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={direction === 'down' ? 'M4 6l4 4 4-4' : 'M4 10l4-4 4 4'} />
    </svg>
  )
}

/** A labelled section inside the sheet (uppercase label). */
export function SheetSection({ heading, children }: { heading: string; children: ReactNode }) {
  const id = useId()
  return (
    <section aria-labelledby={id} className="mt-7">
      <h3 id={id} className="mb-3 text-[11px] font-medium uppercase tracking-[0.12em] text-muted">{heading}</h3>
      {children}
    </section>
  )
}

export function ContinueInChat({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="-ml-2 mt-2 inline-flex min-h-11 items-center px-2 text-[13px] text-primary underline underline-offset-4 transition-colors hover:text-cyan"
    >
      Continue in chat →
    </button>
  )
}

/**
 * The 44px bar shown while nothing is selected. aria-disabled rather than
 * disabled, so it stays focusable and screen-reader users still hear the
 * hint; focus moves here when a sheet closes. While a sheet covers it, it is
 * inert (it keeps its place, so opening the sheet never resizes the region).
 */
export function LockedBar({ hint, barRef, covered }: { hint: string; barRef: Ref<HTMLButtonElement>; covered: boolean }) {
  return (
    <button
      ref={barRef}
      type="button"
      aria-disabled="true"
      inert={covered}
      className="flex min-h-11 w-full shrink-0 cursor-not-allowed items-center justify-between gap-3 border-t border-hairline px-4 text-left text-xs text-muted md:px-7"
    >
      <span className="min-w-0 truncate">{hint}</span>
      <span className="flex shrink-0 items-center opacity-40">
        <Chevron direction="up" />
      </span>
    </button>
  )
}

type DetailsSheetProps = {
  /** Accessible name of the region, e.g. "Cache details". */
  label: string
  /** Accessible name of the chevron, e.g. "Close Cache details and deselect it". */
  closeLabel: string
  onClose: () => void
  sheetRef?: Ref<HTMLDivElement>
  scrollRef?: Ref<HTMLDivElement>
  children: ReactNode
}

/**
 * While it is open, a tap anywhere in the strip above it closes it (owner,
 * 2026-10-02): the cards or diagram nodes there ignore pointers.
 */
export function DetailsSheet({ label, closeLabel, onClose, sheetRef, scrollRef, children }: DetailsSheetProps) {
  return (
    <>
      {/* Dims the strip above the sheet so it reads as behind. Pass-through:
          a tap there reaches the grid or diagram behind it, which closes the
          sheet. */}
      <div aria-hidden="true" className="sheet-scrim pointer-events-none absolute inset-0 z-[9] bg-canvas/45" />
      <div
        ref={sheetRef}
        role="region"
        aria-label={label}
        className="sheet-up absolute inset-x-0 bottom-0 top-[20%] z-10 flex flex-col rounded-t-[10px] border-t border-hairline bg-panel shadow-[0_-12px_32px_rgba(0,0,0,0.55)]"
      >
        <div className="relative flex min-h-11 shrink-0 items-center justify-center">
          <span aria-hidden="true" className="h-1 w-10 rounded-full bg-hairline" />
          <button
            type="button"
            aria-label={closeLabel}
            onClick={onClose}
            className="absolute right-1 top-0 flex size-11 items-center justify-center rounded-[3px] text-muted transition-colors hover:text-primary focus-visible:outline-1 focus-visible:outline-cyan md:right-3"
          >
            <Chevron direction="down" />
          </button>
        </div>
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-5 pb-8 md:px-8">
          {children}
        </div>
      </div>
    </>
  )
}
