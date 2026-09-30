import type { RefObject } from 'react'
import { architectureNodes, type NodeId } from '../architecture'

type PipelineStripProps = {
  onViewArchitecture: () => void
  activeNode?: NodeId | null
  /**
   * Ref attached to the trigger button so the architecture sheet can restore
   * focus here when it closes (it opens with `aria-modal="true"`, moving
   * focus into the dialog).
   */
  triggerRef?: RefObject<HTMLButtonElement | null>
}

function PipelineStrip({ onViewArchitecture, activeNode, triggerRef }: PipelineStripProps) {
  return (
    <section aria-label="Architecture pipeline" className="shrink-0 border-t border-hairline bg-panel px-4 py-1.5 md:hidden">
      <div className="mb-1 flex items-center justify-between gap-3">
        <span className="text-xs text-muted">Pipeline</span>
        <button
          ref={triggerRef}
          type="button"
          onClick={onViewArchitecture}
          className="-my-3 -mr-2 inline-flex min-h-11 items-center px-2 text-xs text-primary underline underline-offset-4 hover:text-cyan"
        >
          View architecture
        </button>
      </div>
      <ol className="flex gap-4 overflow-x-auto pb-1">
        {architectureNodes.map((node) => (
          <li key={node.id} className="flex shrink-0 flex-col items-center gap-1 text-center text-[11px] text-muted">
            <span aria-hidden="true" className={`h-2 w-2 rounded-full border ${node.id === activeNode ? 'border-cyan bg-cyan' : 'border-muted'}`} />
            <span className="whitespace-nowrap">{node.data.label}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}

export default PipelineStrip
