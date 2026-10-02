import assert from 'node:assert/strict'
import { test } from 'node:test'
import { closesOnFocusOut, closesOnPointerDown, takesEscape } from './popover.ts'

const trigger = { name: 'trigger' }
const copyButton = { name: 'copy button' }
const askBox = { name: 'ask box' }
const page = { name: 'page' }
const item = { contains: (node: never) => node === trigger || node === copyButton }

test('a tap on the trigger or inside the popover does not close it from outside', () => {
  // The trigger's own click toggles; the Copy email button keeps it open.
  assert.equal(closesOnPointerDown(trigger, item), false)
  assert.equal(closesOnPointerDown(copyButton, item), false)
})

test('a tap anywhere else closes it, including when the item is gone', () => {
  assert.equal(closesOnPointerDown(page, item), true)
  assert.equal(closesOnPointerDown(askBox, item), true)
  assert.equal(closesOnPointerDown(page, null), true)
})

test('focus moving to a control outside (Tab past it, the phone ask box in focus mode) closes it', () => {
  assert.equal(closesOnFocusOut(askBox, item), true)
  assert.equal(closesOnFocusOut(page, null), true)
})

test('focus moving inside the item, or to nowhere (a click on the popover text), keeps it open', () => {
  assert.equal(closesOnFocusOut(copyButton, item), false)
  assert.equal(closesOnFocusOut(trigger, item), false)
  assert.equal(closesOnFocusOut(null, item), false)
})

test('Escape belongs to the open popover unless another handler used it or an IME is composing', () => {
  const escape = { key: 'Escape', defaultPrevented: false }
  assert.equal(takesEscape(escape), true)
  assert.equal(takesEscape({ ...escape, defaultPrevented: true }), false)
  assert.equal(takesEscape({ ...escape, isComposing: true }), false)
  assert.equal(takesEscape({ ...escape, keyCode: 229 }), false)
  assert.equal(takesEscape({ key: 'Enter', defaultPrevented: false }), false)
})
