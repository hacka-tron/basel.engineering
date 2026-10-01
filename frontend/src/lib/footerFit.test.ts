import assert from 'node:assert/strict'
import { test } from 'node:test'
import { NEW_CHAT_GAP_PX, newChatFit } from './footerFit.ts'

const base = { available: 328, stats: 150, prefix: 79, stressControl: 34, label: 82, compact: 32 }
const exactFor = (button: number, stats = base.stats) => stats + base.stressControl + button + NEW_CHAT_GAP_PX * 2

test('the label shows when it fits with breathing room on both sides', () => {
  assert.equal(newChatFit({ ...base, available: exactFor(base.label) }), 'label')
})

test('one pixel short of the label switches to the compact button', () => {
  assert.equal(newChatFit({ ...base, available: exactFor(base.label) - 1 }), 'compact')
  assert.equal(newChatFit({ ...base, available: exactFor(base.compact) }), 'compact')
})

test('when even the compact button does not fit, the timing prefix makes room', () => {
  assert.equal(newChatFit({ ...base, available: exactFor(base.compact) - 1 }), 'tight')
})

test('without a prefix to drop, the compact button is the floor', () => {
  assert.equal(newChatFit({ ...base, prefix: 0, available: exactFor(base.compact) - 1 }), 'compact')
})

test('longer stats take the room first', () => {
  assert.equal(newChatFit(base), 'label')
  assert.equal(newChatFit({ ...base, stats: 230 }), 'compact')
  assert.equal(newChatFit({ ...base, stats: 270 }), 'tight')
})
