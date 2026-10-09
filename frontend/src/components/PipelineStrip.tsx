import { useEffect, useRef, type RefObject } from 'react'
import { architectureNodes, type NodeId } from '../architecture'
import { OTHER_VIEWS, type MobileView, type OtherView } from '../lib/diagramNav'
import { PORTFOLIO_STATUS_HINT } from '../lib/portfolioView'
export type { MobileView }

type PipelineStripProps = {
  view: MobileView
  onViewChange: (view: MobileView) => void
  activeNode?: NodeId | null
  /** The Diagram and Projects segments; focus returns to the one whose view closes. */
  toggleRefs?: Record<OtherView, RefObject<HTMLButtonElement | null>>
  /** The non-Chat views offered: the current topic's panel only (App.tsx), so the switch is Chat | Projects or Chat | Diagram. */
  views?: readonly OtherView[]
}

// The `portfolio` view is the project grid, labelled Projects (owner, 2026-10-09).
const LABELS: Record<MobileView, string> = { chat: 'Chat', diagram: 'Diagram', portfolio: 'Projects' }

/** Below 360px the segments are icons: a speech bubble, a node graph, a 2x2 grid. */
function ViewIcon({ view }: { view: MobileView }) {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {view === 'chat' && <path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" />}
      {view === 'diagram' && <><rect x="1.5" y="2" width="5" height="3.5" rx="0.5" /><rect x="9.5" y="2" width="5" height="3.5" rx="0.5" /><rect x="5.5" y="10.5" width="5" height="3.5" rx="0.5" /><path d="M6.5 3.75h3M12 5.5v2.5H8v2.5" /></>}
      {view === 'portfolio' && <><rect x="2" y="2" width="5" height="5" rx="0.5" /><rect x="9" y="2" width="5" height="5" rx="0.5" /><rect x="2" y="9" width="5" height="5" rx="0.5" /><rect x="9" y="9" width="5" height="5" rx="0.5" /></>}
    </svg>
  )
}

/**
 * Below md, the row above the ask box: the live pipeline stages (no heading;
 * the labelled dots explain themselves) and the topic's view switch (owner,
 * 2026-10-09, option A): Chat | Projects under About Basel, Chat | Diagram
 * under About This System. The project grid and the diagram replace the
 * conversation in place, so the ask box stays usable while a request runs.
 */
function PipelineStrip({ view, onViewChange, activeNode, toggleRefs, views = OTHER_VIEWS }: PipelineStripProps) {
  const options: readonly MobileView[] = ['chat', ...views]
  // Three segments would need icons below 360px; two (Chat | Projects, Chat | Diagram) fit as text down to 280px.
  const compact = options.length > 2
  const stagesRef = useRef<HTMLOListElement>(null)
  const activeLabel = architectureNodes.find((node) => node.id === activeNode)?.data.label

  // Keep the running stage in view as a request walks the pipeline. Scrolls
  // only the strip itself (scrollIntoView could also scroll the page shell).
  useEffect(() => {
    const list = stagesRef.current
    const item = activeNode ? list?.querySelector<HTMLElement>(`[data-stage="${activeNode}"]`) : null
    if (!list || !item) return
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    list.scrollTo({ left: item.offsetLeft - (list.clientWidth - item.offsetWidth) / 2, behavior: reduceMotion ? 'auto' : 'smooth' })
  }, [activeNode])

  return (
    <section aria-label="Architecture pipeline" className="flex shrink-0 items-center gap-3 border-t border-hairline bg-panel pl-4 pr-2 md:hidden">
      <div className="min-w-0 flex-1">
        {view === 'chat' ? (
          <ol
            ref={stagesRef}
            aria-label="Pipeline stages"
            className="flex min-w-0 items-center gap-3.5 overflow-x-auto py-1 [mask-image:linear-gradient(to_right,black_82%,transparent)] [scrollbar-width:none]"
          >
            {architectureNodes.map((node) => {
              const active = node.id === activeNode
              return (
                <li key={node.id} data-stage={node.id} aria-current={active ? 'step' : undefined} className="flex shrink-0 flex-col items-center gap-1 text-center text-[11px] leading-none text-muted">
                  <span aria-hidden="true" className={`size-2 rounded-full border transition-colors ${active ? 'border-cyan bg-cyan' : 'border-muted'}`} />
                  <span className={`whitespace-nowrap ${active ? 'text-cyan' : ''}`}>{node.data.label}</span>
                </li>
              )
            })}
          </ol>
        ) : (
          <p className="flex min-w-0 items-center gap-2 text-[11px] text-muted" aria-live="polite">
            {activeLabel ? (
              <>
                <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-cyan" />
                <span className="truncate text-cyan">{activeLabel}</span>
              </>
            ) : (
              <span className="truncate">{view === 'portfolio' ? PORTFOLIO_STATUS_HINT : 'Select a component'}</span>
            )}
          </p>
        )}
      </div>
      <div role="group" aria-label="View" className="flex shrink-0 rounded-[3px] border border-hairline p-0.5 text-xs">
        {options.map((option) => (
          <button
            key={option}
            ref={option === 'chat' ? undefined : toggleRefs?.[option]}
            type="button"
            aria-pressed={view === option}
            aria-label={LABELS[option]}
            title={LABELS[option]}
            onClick={() => onViewChange(option)}
            // Visibly 40px tall inside the 2px-padded, bordered group (46px
            // outline). The tap area is 48px tall: the pseudo-element reaches
            // 4px above and below the segment, past the group's border, and
            // sideways to the group's outer edges, so a tap anywhere on the
            // control hits a segment. Making the segments h-11 instead would
            // grow the strip by 4px and take it from the messages. From 360px
            // the segments are text (about 222px); below 360px 44px-wide icons.
            className={`relative inline-flex h-10 items-center justify-center rounded-[2px] px-3 transition-colors after:absolute after:inset-x-0 after:-inset-y-1 after:content-[''] first:after:-left-[3px] last:after:-right-[3px] focus-visible:outline-1 focus-visible:outline-cyan ${compact ? 'max-[360px]:w-11 max-[360px]:px-0' : ''} ${view === option ? 'bg-canvas text-cyan' : 'text-muted hover:text-primary'}`}
          >
            <span className={compact ? 'max-[360px]:hidden' : undefined}>{LABELS[option]}</span>
            {compact && <span className="min-[360px]:hidden"><ViewIcon view={option} /></span>}
          </button>
        ))}
      </div>
    </section>
  )
}

export default PipelineStrip
