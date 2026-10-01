// Diagram selection and the phone details panel.
//
// The details panel belongs to the selected component. With nothing selected
// there is nothing to show (the chunks and answer of the latest chat question
// read as unrelated there), so on phones the bar under the diagram is locked:
// closed, aria-disabled, and it says "Select a component for details". The
// panel is open exactly while a component is selected; its close chevron
// deselects (owner, 2026-10-01), as do a tap on empty diagram space and Escape.

export const PORTRAIT_DETAILS_HINT = 'Select a component for details'

type KeyEventLike = { key: string; defaultPrevented: boolean; isComposing?: boolean }

/**
 * Escape clears the selection before it does anything else in the diagram:
 * on phones the first Escape deselects and the next one returns to Chat
 * (`diagramNav`, which skips events that were already handled). Nothing to
 * deselect, a key another handler already used, or an Escape that ends an
 * IME composition in the ask box leaves the event alone.
 */
export function deselectsOnKey(event: KeyEventLike, hasSelection: boolean): boolean {
  return hasSelection && event.key === 'Escape' && !event.defaultPrevented && !event.isComposing
}
