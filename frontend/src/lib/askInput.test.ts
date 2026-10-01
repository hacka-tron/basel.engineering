import assert from 'node:assert/strict'
import { test } from 'node:test'
import { askButtonMode, caretOnFirstLine, lastSentQuestion, shouldRecallQuestion, type ArrowUpContext } from './askInput.ts'
import type { ChatMessage } from './conversation.ts'

const bare: ArrowUpContext = { key: 'ArrowUp', value: '', caret: 0, composing: false, modified: false }

test('a bare Up arrow in an empty input recalls', () => {
  assert.equal(shouldRecallQuestion(bare), true)
  assert.equal(shouldRecallQuestion({ ...bare, caret: null }), true)
})

test('Up is not hijacked when the input has text', () => {
  assert.equal(shouldRecallQuestion({ ...bare, value: 'draft', caret: 5 }), false)
  assert.equal(shouldRecallQuestion({ ...bare, value: ' ', caret: 1 }), false)
})

test('Up during IME composition or with a modifier is left alone', () => {
  assert.equal(shouldRecallQuestion({ ...bare, composing: true }), false)
  assert.equal(shouldRecallQuestion({ ...bare, modified: true }), false)
})

test('other keys never recall', () => {
  assert.equal(shouldRecallQuestion({ ...bare, key: 'ArrowDown' }), false)
  assert.equal(shouldRecallQuestion({ ...bare, key: 'Up' }), false)
})

test('caretOnFirstLine', () => {
  assert.equal(caretOnFirstLine('', 0), true)
  assert.equal(caretOnFirstLine('one line', 4), true)
  assert.equal(caretOnFirstLine('first\nsecond', 3), true)
  assert.equal(caretOnFirstLine('first\nsecond', 8), false)
  assert.equal(caretOnFirstLine('first\nsecond', null), false)
})

test('lastSentQuestion picks the latest user message of this conversation', () => {
  const messages: ChatMessage[] = [
    { id: 'u1', role: 'user', content: 'First?', createdAt: 1 },
    { id: 'a1', role: 'assistant', content: 'Answer', state: 'done', createdAt: 1 },
    { id: 'u2', role: 'user', content: 'Second?', createdAt: 2 },
    { id: 'a2', role: 'assistant', content: 'Oops', state: 'error', createdAt: 2 },
  ]
  assert.equal(lastSentQuestion(messages), 'Second?')
  assert.equal(lastSentQuestion([]), null)
  assert.equal(lastSentQuestion([{ id: 'a', role: 'assistant', content: 'hi', state: 'done', createdAt: 1 }]), null)
})

test('askButtonMode: Send when idle, Stop or stop-and-send while streaming', () => {
  assert.equal(askButtonMode(false, ''), 'send')
  assert.equal(askButtonMode(false, 'q'), 'send')
  assert.equal(askButtonMode(true, ''), 'stop')
  assert.equal(askButtonMode(true, '   '), 'stop')
  assert.equal(askButtonMode(true, 'next question'), 'stop-and-send')
})
