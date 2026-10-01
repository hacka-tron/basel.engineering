// Portrait (phone) Diagram view: the details panel under the diagram.
//
// It belongs to the selected component. With nothing selected there is nothing
// to show (the chunks and answer of the latest chat question read as unrelated
// there), so the toggle bar is locked: closed, aria-disabled, and it says
// "Select a component for details" instead of "Details / N chunks".

export type PortraitDetailsState = 'open' | 'collapsed' | 'locked'

export const PORTRAIT_DETAILS_HINT = 'Select a component for details'

/**
 * `detailsOpen` is the visitor's last open/collapse choice; it only takes
 * effect while a component is selected, so clearing the selection (a typed
 * question, New chat) closes the panel without forgetting the choice.
 */
export function portraitDetailsState(selected: boolean, detailsOpen: boolean): PortraitDetailsState {
  if (!selected) return 'locked'
  return detailsOpen ? 'open' : 'collapsed'
}
