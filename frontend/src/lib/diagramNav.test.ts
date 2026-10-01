import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createDiagramNav, type MobileView } from './diagramNav.ts'

// A fake browser: a history stack, a focused element, and a frame queue.
function setup() {
  const stack: unknown[] = [null]
  let index = 0
  let view: MobileView = 'chat'
  let focused = 'chat-segment'
  const frames: Array<() => void> = []
  const log: string[] = []
  const history = {
    get state() { return stack[index] },
    pushState(state: unknown) { stack.splice(index + 1); stack.push(state); index++; log.push('push') },
    back() { log.push('back') },
  }
  const nav = createDiagramNav({
    history,
    setView: (next) => { view = next },
    afterRender: (cb) => frames.push(cb),
    focusDiagramToggle: () => { focused = 'diagram-toggle' },
  })
  return {
    nav, log,
    get view() { return view },
    get focused() { return focused },
    focus(id: string) { focused = id },
    flush() { frames.splice(0).forEach((cb) => cb()) },
    // What the browser does for history.back(): move and fire popstate.
    back() { index--; nav.handlePopState(stack[index]) },
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
