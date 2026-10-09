// History and focus for the phone views (spec 2026-10-02 §5.3): Chat and
// the two panels, Diagram and Projects (internal id `portfolio`), each shown
// under its own topic since 2026-10-09 (App.tsx). Each non-Chat view is one
// history entry above Chat: opening one from Chat pushes it, switching
// between the panels (say, a stress tap from Projects) replaces it, so Back always returns to Chat and Forward reopens the last
// non-Chat view. Every way back to Chat (Back/popstate, Escape, the Chat
// segment, "Continue in chat") moves focus to the toggle segment of the view
// that was left, because whatever had focus (including the Chat segment
// itself) is about to be unmounted or is no longer the control that opened
// the view, and closes that view's details sheet (onReturnToChat; no extra
// history entry is used for a sheet).

export type MobileView = 'chat' | 'diagram' | 'portfolio'
export type OtherView = Exclude<MobileView, 'chat'>

export const OTHER_VIEWS: readonly OtherView[] = ['diagram', 'portfolio']

export interface ViewNavDeps {
  history: Pick<History, 'state' | 'pushState' | 'replaceState' | 'back'>
  setView: (view: MobileView) => void
  /** Runs after the view has re-rendered (e.g. requestAnimationFrame). */
  afterRender: (callback: () => void) => void
  /** Focuses the toggle segment of `view`. */
  focusViewToggle: (view: OtherView) => void
  /** Called once whenever the view returns to Chat, by any route. */
  onReturnToChat?: () => void
  /** The non-Chat views offered (Projects is left out until there is a published project); others read as Chat. */
  views?: readonly OtherView[]
}

/** The view a history entry holds; an unknown or hidden view (say, Projects with no published project) reads as Chat. */
export function viewFromHistoryState(state: unknown, views: readonly OtherView[] = OTHER_VIEWS): MobileView {
  const view = (state as { glassboxView?: unknown } | null)?.glassboxView
  return views.includes(view as OtherView) ? view as OtherView : 'chat'
}

export function createViewNav(deps: ViewNavDeps) {
  const views = deps.views ?? OTHER_VIEWS
  const viewOf = (state: unknown) => viewFromHistoryState(state, views)
  const initial = viewOf(deps.history.state)
  // The non-Chat view shown last. A reload inside one restores it from history.
  let lastView: OtherView = initial === 'chat' ? 'diagram' : initial

  function returnToChat() {
    const left = lastView
    deps.afterRender(() => deps.focusViewToggle(left))
    deps.setView('chat')
    deps.onReturnToChat?.()
  }

  function showView(view: MobileView) {
    const current = viewOf(deps.history.state)
    if (view !== 'chat' && !views.includes(view)) return
    if (view !== 'chat') {
      lastView = view
      if (current === 'chat') deps.history.pushState({ glassboxView: view }, '')
      else if (current !== view) deps.history.replaceState({ glassboxView: view }, '')
      deps.setView(view)
      return
    }
    // From a view entry, go back; popstate then returns to Chat.
    if (current !== 'chat') deps.history.back()
    else returnToChat()
  }

  /** Every stress-test tap (including one ignored during the countdown or an
   * in-flight request) shows the workers: open the Diagram on phones (no
   * extra history entry if a view is already shown); desktop shows it always. */
  function revealDiagram(isDesktop: boolean) {
    if (!isDesktop) showView('diagram')
  }

  function handlePopState(state: unknown) {
    const view = viewOf(state)
    if (view === 'chat') {
      returnToChat()
      return
    }
    lastView = view
    deps.setView(view)
  }

  function handleKeyDown(event: { key: string; defaultPrevented: boolean }) {
    if (event.key === 'Escape' && !event.defaultPrevented) showView('chat')
  }

  return { showView, revealDiagram, handlePopState, handleKeyDown }
}
