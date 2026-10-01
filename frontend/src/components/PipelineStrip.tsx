import { useEffect, useRef, type RefObject } from 'react'
import { architectureNodes, type NodeId } from '../architecture'

import type { MobileView } from '../lib/diagramNav'
export type { MobileView }

type PipelineStripProps = {
  view: MobileView
  onViewChange: (view: MobileView) => void
  activeNode?: NodeId | null
  /** The Diagram toggle; focus returns here when the diagram view closes. */
  diagramButtonRef?: RefObject<HTMLButtonElement | null>
}

/**
 * Below md, the row above the ask box: the live pipeline stages (no heading;
 * the labelled dots explain themselves) and a Chat | Diagram switch. The
 * diagram replaces the conversation in place, so the ask box stays usable
 * while a request runs through it.
 */
function PipelineStrip({ view, onViewChange, activeNode, diagramButtonRef }: PipelineStripProps) {
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
              <span className="truncate">Waiting for a question</span>
            )}
          </p>
        )}
      </div>
      <div role="group" aria-label="View" className="flex shrink-0 rounded-[3px] border border-hairline p-0.5 text-xs">
        {(['chat', 'diagram'] as const).map((option) => (
          <button
            key={option}
            ref={option === 'diagram' ? diagramButtonRef : undefined}
            type="button"
            aria-pressed={view === option}
            onClick={() => onViewChange(option)}
            // 40px tall inside the 2px-padded group; the pseudo-element
            // extends the tap area to 48px.
            className={`relative inline-flex h-10 items-center rounded-[2px] px-3 transition-colors after:absolute after:inset-x-0 after:-inset-y-1 after:content-[''] focus-visible:outline-1 focus-visible:outline-cyan ${view === option ? 'bg-canvas text-cyan' : 'text-muted hover:text-primary'}`}
          >
            {option === 'chat' ? 'Chat' : 'Diagram'}
          </button>
        ))}
      </div>
    </section>
  )
}

export default PipelineStrip
