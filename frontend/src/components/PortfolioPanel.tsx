// The Portfolio topic's panel (spec 2026-10-02 §5.4, §5.5): the desktop right
// pane while the topic is Portfolio, and the phone Portfolio view. A card
// grid; selecting a card opens the shared details sheet over 80% of the panel
// with the card scrolled into the strip above it. Mirrors the diagram: a
// locked bar while nothing is selected, Escape deselects first. While the
// sheet is open (owner, 2026-10-02): on phones any tap in the strip closes it
// (the cards there are inert, so an accidental tap never asks a new,
// rate-limited question); on desktop another card switches to that project,
// the selected card or empty space closes it. Focus: opening from the
// keyboard moves into the sheet (its chevron); closing with the chevron or
// Escape moves to the locked bar.
import { useCallback, useEffect, useRef } from 'react'
import { deselectsOnKey } from '../lib/detailsPanel'
import { scrollTopForItem } from '../lib/detailsSheet'
import type { Project } from '../lib/portfolio'
import { PORTFOLIO_DETAILS_HINT, PORTFOLIO_EMPTY_TEXT, portfolioHeading } from '../lib/portfolioView'
import { DetailsSheet, LockedBar } from './DetailsSheet'
import ProjectCard from './ProjectCard'
import ProjectDetails from './ProjectDetails'
import { VisualsGallery } from './PortfolioVisuals'

type PortfolioPanelProps = {
  projects: readonly Project[]
  /** The phone Portfolio view: cards are inert under an open sheet, and any strip tap closes it. */
  phone?: boolean
  selectedSlug: string | null
  /** Phones only: the selected project's streamed answer ("Ask about this"). */
  answerText?: string | null
  onSelect: (slug: string) => void
  onDeselect: () => void
  /** Phones only: leave the Portfolio view for the conversation. */
  onContinueInChat?: () => void
  /** Receives the first card, or the empty-state text, for "See portfolio →". */
  focusTargetRef?: { current: HTMLElement | null }
}

function PortfolioPanel({ projects, phone = false, selectedSlug, answerText, onSelect, onDeselect, onContinueInChat, focusTargetRef }: PortfolioPanelProps) {
  const selected = projects.find((project) => project.slug === selectedSlug) ?? null
  const hasSelection = selected !== null
  const sheetRef = useRef<HTMLDivElement>(null)
  const sheetScrollRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const lockedBarRef = useRef<HTMLButtonElement>(null)
  // Closing from inside the sheet (the chevron, or Escape with focus in it)
  // moves focus to the locked bar instead of letting it fall to <body>.
  const focusLockedBarRef = useRef(false)
  const setFocusTarget = useCallback((element: HTMLElement | null) => {
    if (focusTargetRef) focusTargetRef.current = element
  }, [focusTargetRef])

  // 'sheet': the chevron. 'key': Escape; focus goes to the locked bar unless
  // it is somewhere else that still exists (not in the sheet, the cards, or
  // lost to <body>). 'pointer': a tap in the strip leaves focus where it put it.
  const deselect = useCallback((via: 'sheet' | 'key' | 'pointer') => {
    const active = document.activeElement
    const lost = !active || active === document.body || sheetRef.current?.contains(active) || listRef.current?.contains(active)
    if (via === 'sheet' || (via === 'key' && lost)) focusLockedBarRef.current = true
    onDeselect()
  }, [onDeselect])
  useEffect(() => {
    if (selectedSlug !== null || !focusLockedBarRef.current) return
    focusLockedBarRef.current = false
    lockedBarRef.current?.focus()
  }, [selectedSlug])

  // Opening from the keyboard (or on phones, where the card goes inert and its
  // focus falls to <body>) moves focus into the sheet, onto its chevron.
  useEffect(() => {
    if (selectedSlug === null) return
    const active = document.activeElement
    if (active && active !== document.body && !listRef.current?.contains(active)) return
    sheetRef.current?.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true })
  }, [selectedSlug])

  // The selected card moves to the top of the grid, into the strip the sheet
  // leaves uncovered. Scrolls the list only, never the page.
  useEffect(() => {
    if (sheetScrollRef.current) sheetScrollRef.current.scrollTop = 0
    if (selectedSlug === null) return
    const frame = requestAnimationFrame(() => {
      const list = listRef.current
      const card = list?.querySelector<HTMLElement>(`[data-project="${CSS.escape(selectedSlug)}"]`)
      if (!list || !card) return
      const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      list.scrollTo({ top: scrollTopForItem(list.scrollTop, list.getBoundingClientRect().top, card.getBoundingClientRect().top), behavior: reduceMotion ? 'auto' : 'smooth' })
    })
    return () => cancelAnimationFrame(frame)
  }, [selectedSlug])

  // Escape deselects first (document capture, before the phone view's
  // Escape-returns-to-Chat listener), exactly like the diagram.
  useEffect(() => {
    if (!hasSelection) return
    function handleKeyDown(event: KeyboardEvent) {
      if (!deselectsOnKey(event, true)) return
      event.preventDefault()
      deselect('key')
    }
    document.addEventListener('keydown', handleKeyDown, true)
    return () => document.removeEventListener('keydown', handleKeyDown, true)
  }, [hasSelection, deselect])

  return (
    <section aria-label="Projects" className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-panel">
      {/* Desktop title bar; on phones the toggle already says where you are. */}
      <div className="flex shrink-0 items-center max-md:sr-only md:min-h-14 md:border-b md:border-hairline md:px-7">
        <h2 className="text-xs font-medium text-primary">{portfolioHeading(projects.length)}</h2>
      </div>
      <div
        ref={listRef}
        // Phones: while the sheet is open the cards ignore pointers (and leave
        // the Tab order), so any tap in the strip lands here and closes it.
        // Desktop: a card switches; empty space closes.
        onClick={hasSelection ? (event) => {
          if (phone || !(event.target as HTMLElement).closest('button, a')) deselect('pointer')
        } : undefined}
        className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-4 py-3 md:px-7 md:py-5"
      >
        {projects.length === 0 ? (
          <p ref={setFocusTarget} tabIndex={-1} className="max-w-[65ch] text-[13px] leading-[1.6] text-muted outline-none">{PORTFOLIO_EMPTY_TEXT}</p>
        ) : (
          <ul inert={phone && hasSelection} className={`grid grid-cols-1 gap-3 min-[390px]:grid-cols-2 min-[390px]:gap-2 md:gap-4 xl:grid-cols-3 ${phone && hasSelection ? 'pointer-events-none' : ''}`}>
            {projects.map((project, index) => (
              <li key={project.slug} data-project={project.slug} className="min-w-0">
                <ProjectCard
                  project={project}
                  selected={project.slug === selectedSlug}
                  // Desktop: a second click on the selected card closes the sheet.
                  onPress={() => (project.slug === selectedSlug ? deselect('pointer') : onSelect(project.slug))}
                  buttonRef={index === 0 ? setFocusTarget : undefined}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
      <LockedBar hint={PORTFOLIO_DETAILS_HINT} barRef={lockedBarRef} covered={hasSelection} />
      {selected && (
        <DetailsSheet
          label={`${selected.title} details`}
          closeLabel={`Close ${selected.title} details and deselect it`}
          onClose={() => deselect('sheet')}
          sheetRef={sheetRef}
          scrollRef={sheetScrollRef}
        >
          {/* Keyed: a new project starts with fresh section state (gallery position). */}
          <ProjectDetails key={selected.slug} project={selected} answerText={answerText} onContinueInChat={onContinueInChat} visuals={<VisualsGallery project={selected} />} />
        </DetailsSheet>
      )}
    </section>
  )
}

export default PortfolioPanel
