// Who gets an Escape keypress. Listeners that use one up call preventDefault,
// and later listeners skip handled events:
//   1. a showing footer tooltip closes (window, capture phase; this file);
//      otherwise an open footer "Open to work" popover closes and focus
//      returns to its trigger (window, capture phase, listening only while
//      open; `takesEscape` in lib/popover.ts, components/OpenToWork.tsx),
//   2. an open portfolio lightbox closes, and nothing else (window, capture
//      phase, registered while it is open; components/PortfolioVisuals.tsx),
//   3. a selected diagram component or portfolio project is deselected
//      (document, capture phase; `deselectsOnKey` in lib/detailsPanel.ts,
//      used by ArchitecturePanel and PortfolioPanel),
//   4. the phone Diagram or Projects view returns to Chat (document, bubble;
//      createViewNav in lib/diagramNav.ts).

type ControlLike = { contains: (node: never) => boolean } | null
type ActiveLike = { matches: (selector: string) => boolean } | null

/**
 * A footer tooltip is showing if its press/long-press state is open, or its
 * control has keyboard focus (the CSS `group-focus-visible` tooltip). Only then
 * does Escape belong to the tooltip; a hover tooltip or a mouse-focused
 * control without a tooltip leaves Escape to the next handler.
 */
export function tooltipShowing(open: boolean, control: ControlLike, active: ActiveLike = document.activeElement): boolean {
  if (open) return true
  if (!control || !active) return false
  return (control.contains as (node: unknown) => boolean)(active) && active.matches(':focus-visible')
}
