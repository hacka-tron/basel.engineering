import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createViewNav, type MobileView } from './diagramNav.ts'

// A fake browser: a history stack, a focused element, and a frame queue.
function setup(initialState: unknown = null) {
  const stack: unknown[] = [initialState]
  let index = 0
  let view: MobileView = 'chat'
  let focused = 'chat-segment'
  let returns = 0
  const frames: Array<() => void> = []
  const log: string[] = []
  const history = {
    get state() { return stack[index] },
    pushState(state: unknown) { stack.splice(index + 1); stack.push(state); index++; log.push('push') },
    replaceState(state: unknown) { stack[index] = state; log.push('replace') },
    back() { log.push('back') },
  }
  const nav = createViewNav({
    history,
    setView: (next) => { view = next },
    afterRender: (cb) => frames.push(cb),
    focusViewToggle: (toggle) => { focused = `${toggle}-toggle` },
    onReturnToChat: () => { returns++ },
  })
  return {
    nav, log,
    get view() { return view },
    get focused() { return focused },
    get returns() { return returns },
    focus(id: string) { focused = id },
    flush() { frames.splice(0).forEach((cb) => cb()) },
    // What the browser does for Back and Forward: move and fire popstate.
    back() { index--; nav.handlePopState(stack[index]) },
    forward() { index++; nav.handlePopState(stack[index]) },
  }
}

function openDiagram(t: ReturnType<typeof setup>) {
  t.nav.showView('diagram')
  assert.equal(t.view, 'diagram')
  t.flush()
}

test('opening the diagram pushes one history entry, not two', () => {
  const t = setup()
  openDiagram(t)
  t.nav.showView('diagram')
  assert.deepEqual(t.log, ['push'])
})

test('Chat segment returns to chat and focuses the Diagram toggle even though the Chat segment has focus', () => {
  const t = setup()
  openDiagram(t)
  t.focus('chat-segment')
  t.nav.showView('chat')
  t.back()
  t.flush()
  assert.equal(t.view, 'chat')
  assert.equal(t.focused, 'diagram-toggle')
})

test('Escape returns to chat and focuses the Diagram toggle', () => {
  const t = setup()
  openDiagram(t)
  t.focus('diagram-node')
  t.nav.handleKeyDown({ key: 'Escape', defaultPrevented: false })
  t.back()
  t.flush()
  assert.equal(t.view, 'chat')
  assert.equal(t.focused, 'diagram-toggle')
})

test('Escape is ignored when something already handled it, or for other keys', () => {
  const t = setup()
  openDiagram(t)
  t.nav.handleKeyDown({ key: 'Escape', defaultPrevented: true })
  t.nav.handleKeyDown({ key: 'Enter', defaultPrevented: false })
  assert.equal(t.view, 'diagram')
  assert.deepEqual(t.log, ['push'])
})

test('Back (popstate) returns to chat and focuses the Diagram toggle from body focus', () => {
  const t = setup()
  openDiagram(t)
  t.focus('body')
  t.back()
  t.flush()
  assert.equal(t.view, 'chat')
  assert.equal(t.focused, 'diagram-toggle')
})

test('Continue in chat returns to chat and focuses the Diagram toggle', () => {
  const t = setup()
  openDiagram(t)
  t.focus('continue-in-chat')
  t.nav.showView('chat')
  t.back()
  t.flush()
  assert.equal(t.view, 'chat')
  assert.equal(t.focused, 'diagram-toggle')
})

test('returning to chat without a diagram history entry (after a reload) still moves focus', () => {
  const t = setup()
  t.focus('chat-segment')
  t.nav.showView('chat')
  t.flush()
  assert.equal(t.view, 'chat')
  assert.equal(t.focused, 'diagram-toggle')
  assert.deepEqual(t.log, [])
})

test('going back via history.back() does not set the view until popstate fires', () => {
  const t = setup()
  openDiagram(t)
  t.nav.showView('chat')
  assert.equal(t.view, 'diagram')
  assert.deepEqual(t.log, ['push', 'back'])
})

test('revealDiagram opens the diagram on mobile once, and does nothing on desktop', () => {
  const t = setup()
  t.nav.revealDiagram(true)
  assert.equal(t.view, 'chat')
  assert.deepEqual(t.log, [])
  t.nav.revealDiagram(false)
  t.nav.revealDiagram(false)
  assert.equal(t.view, 'diagram')
  assert.deepEqual(t.log, ['push'])
  t.back()
  assert.equal(t.view, 'chat')
})

test('every way back to chat closes the details sheet exactly once', () => {
  for (const leave of ['chat-segment', 'escape', 'back'] as const) {
    const t = setup()
    openDiagram(t)
    assert.equal(t.returns, 0)
    if (leave === 'chat-segment') t.nav.showView('chat')
    if (leave === 'escape') t.nav.handleKeyDown({ key: 'Escape', defaultPrevented: false })
    t.back()
    assert.equal(t.view, 'chat')
    assert.equal(t.returns, 1, leave)
  }
  const reloaded = setup()
  reloaded.nav.showView('chat')
  assert.equal(reloaded.returns, 1)
})

test('Chat -> Portfolio pushes one entry; Portfolio <-> Diagram replaces it', () => {
  const t = setup()
  t.nav.showView('portfolio')
  t.nav.showView('diagram')
  t.nav.showView('portfolio')
  assert.equal(t.view, 'portfolio')
  assert.deepEqual(t.log, ['push', 'replace', 'replace'])
})

test('Back always returns to chat and focuses the segment of the view that was left', () => {
  const t = setup()
  t.nav.showView('diagram')
  t.nav.showView('portfolio')
  t.back()
  t.flush()
  assert.equal(t.view, 'chat')
  assert.equal(t.focused, 'portfolio-toggle')
  assert.equal(t.returns, 1)
})

test('Forward reopens the last non-Chat view', () => {
  const t = setup()
  t.nav.showView('diagram')
  t.nav.showView('portfolio')
  t.back()
  t.forward()
  assert.equal(t.view, 'portfolio')
})

test('Escape from Portfolio goes back to chat and focuses the Portfolio toggle', () => {
  const t = setup()
  t.nav.showView('portfolio')
  t.nav.handleKeyDown({ key: 'Escape', defaultPrevented: false })
  t.back()
  t.flush()
  assert.equal(t.view, 'chat')
  assert.equal(t.focused, 'portfolio-toggle')
})

test('a reload inside Portfolio returns focus to the Portfolio toggle', () => {
  // After a reload the Portfolio entry is the first one the app knows about;
  // the browser's Back fires popstate with the Chat entry's (null) state.
  const t = setup({ glassboxView: 'portfolio' })
  t.focus('chat-segment')
  t.nav.showView('chat')
  assert.deepEqual(t.log, ['back'])
  t.nav.handlePopState(null)
  t.flush()
  assert.equal(t.view, 'chat')
  assert.equal(t.focused, 'portfolio-toggle')
})

test('a stress tap from Portfolio opens the Diagram without a second history entry', () => {
  const t = setup()
  t.nav.showView('portfolio')
  t.nav.revealDiagram(false)
  assert.equal(t.view, 'diagram')
  assert.deepEqual(t.log, ['push', 'replace'])
  t.back()
  assert.equal(t.view, 'chat')
})

test('unknown history states read as chat', () => {
  const t = setup({ glassboxView: 'settings' })
  t.nav.showView('chat')
  assert.equal(t.view, 'chat')
  assert.deepEqual(t.log, [])
})
