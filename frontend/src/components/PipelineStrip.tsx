import type { RefObject } from 'react'
import { architectureNodes } from '../architecture'

type PipelineStripProps = {
  onViewArchitecture: () => void
  /**
   * Ref attached to the trigger button so the architecture sheet can restore
   * focus here when it closes (it opens with `aria-modal="true"`, moving
   * focus into the dialog).
   */
  triggerRef?: RefObject<HTMLButtonElement | null>
}

function PipelineStrip({ onViewArchitecture, triggerRef }: PipelineStripProps) {
  return (
    <section aria-label="Architecture pipeline" className="shrink-0 border-t border-hairline bg-panel px-4 py-3 md:hidden">
      <div className="mb-3 flex items-center justify-between gap-3">
        <span className="text-xs text-muted">Pipeline</span>
        <button
          ref={triggerRef}
          type="button"
          onClick={onViewArchitecture}
          className="text-xs text-primary underline underline-offset-4 hover:text-cyan"
        >
          View architecture
        </button>
      </div>
      <ol className="flex gap-4 overflow-x-auto pb-1">
        {architectureNodes.map((node) => (
          <li key={node.id} className="flex shrink-0 flex-col items-center gap-1 text-center text-[10px] text-muted">
            <span aria-hidden="true" className="h-2 w-2 rounded-full border border-muted" />
            <span className="whitespace-nowrap">{node.data.label}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}

export default PipelineStrip
