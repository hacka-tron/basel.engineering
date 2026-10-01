// History + focus transitions for the mobile diagram view. The diagram is a
// history entry; every way back to the conversation (Back/popstate, Escape,
// the Chat segment, "Continue in chat") must leave focus on the Diagram
// toggle, because whatever had focus (including the Chat segment itself) is
// about to be unmounted or is no longer the control that opened the view.

export type MobileView = 'chat' | 'diagram'

export interface DiagramNavDeps {
  history: Pick<History, 'state' | 'pushState' | 'back'>
  setView: (view: MobileView) => void
  /** Runs after the view has re-rendered (e.g. requestAnimationFrame). */
  afterRender: (callback: () => void) => void
  focusDiagramToggle: () => void
}

export function viewFromHistoryState(state: unknown): MobileView {
  return (state as { glassboxView?: MobileView } | null)?.glassboxView === 'diagram' ? 'diagram' : 'chat'
}

export function createDiagramNav(deps: DiagramNavDeps) {
  const restoreFocus = () => deps.afterRender(deps.focusDiagramToggle)

  function showView(view: MobileView) {
    if (view === 'diagram') {
      if (viewFromHistoryState(deps.history.state) !== 'diagram') {
        deps.history.pushState({ glassboxView: 'diagram' }, '')
      }
      deps.setView('diagram')
      return
    }
    restoreFocus()
    if (viewFromHistoryState(deps.history.state) === 'diagram') {
      // popstate then sets the view.
      deps.history.back()
    } else {
      deps.setView('chat')
    }
  }

  /** Every stress-test tap (including one ignored during the countdown or an
   * in-flight request) shows the workers: open the diagram on mobile (no
   * extra history entry if it is already shown); desktop shows it always. */
  function revealDiagram(isDesktop: boolean) {
    if (!isDesktop) showView('diagram')
  }

  function handlePopState(state: unknown) {
    const view = viewFromHistoryState(state)
    if (view === 'chat') restoreFocus()
    deps.setView(view)
  }

  function handleKeyDown(event: { key: string; defaultPrevented: boolean }) {
    if (event.key === 'Escape' && !event.defaultPrevented) showView('chat')
  }

  return { showView, revealDiagram, handlePopState, handleKeyDown }
}
