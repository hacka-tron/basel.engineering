import { useEffect, useState, type ReactNode } from 'react'

const TRANSITION_MS = 200

/**
 * Below md, slides its child closed to zero height. Used by focus mode (hides
 * the header and footer while the ask box has focus). Animates grid rows
 * rather than height, so the content keeps its natural size and nothing reflows
 * inside it. Content is clipped only while closed or moving, so tooltips that
 * open out of the footer are not cut off once it is fully open. At md+ both
 * wrappers are `display: contents`, so the desktop layout is untouched. The
 * inner wrapper is `min-w-0` so a grid item never grows to its content's
 * min-content width: the header must stay as wide as the screen for the
 * name to switch to its short form on very narrow screens. `after` renders
 * outside the clipped wrapper (the floating header's fade band in App.tsx).
 */
function Collapsible({ open, className = '', after, children }: { open: boolean; className?: string; after?: ReactNode; children: ReactNode }) {
  const [settledOpen, setSettledOpen] = useState(open)
  useEffect(() => {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const timer = window.setTimeout(() => setSettledOpen(open), reduceMotion ? 0 : TRANSITION_MS)
    return () => window.clearTimeout(timer)
  }, [open])
  const clipped = !open || !settledOpen
  return (
    <div
      inert={!open}
      className={`grid shrink-0 transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none md:contents ${open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'} ${className}`}
    >
      <div className={`min-h-0 min-w-0 md:contents ${clipped ? 'overflow-hidden' : ''}`}>{children}</div>
      {after}
    </div>
  )
}

export default Collapsible
