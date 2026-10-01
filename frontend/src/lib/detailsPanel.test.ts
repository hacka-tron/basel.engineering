import assert from 'node:assert/strict'
import { test } from 'node:test'
import { deselectsOnKey } from './detailsPanel.ts'

const escape = { key: 'Escape', defaultPrevented: false }

test('Escape deselects while a component is selected', () => {
  assert.equal(deselectsOnKey(escape, true), true)
})

test('Escape with nothing selected is left for the next handler (back to Chat)', () => {
  assert.equal(deselectsOnKey(escape, false), false)
})

test('other keys never deselect', () => {
  assert.equal(deselectsOnKey({ key: 'Enter', defaultPrevented: false }, true), false)
  assert.equal(deselectsOnKey({ key: 'Esc', defaultPrevented: false }, true), false)
})

test('an Escape another handler already used, or one ending an IME composition, is ignored', () => {
  assert.equal(deselectsOnKey({ ...escape, defaultPrevented: true }, true), false)
  assert.equal(deselectsOnKey({ ...escape, isComposing: true }, true), false)
  assert.equal(deselectsOnKey({ ...escape, keyCode: 229 }, true), false) // Safari IME
  assert.equal(deselectsOnKey({ ...escape, keyCode: 27 }, true), true)
})
