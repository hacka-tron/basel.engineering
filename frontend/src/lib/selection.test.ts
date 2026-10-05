import assert from 'node:assert/strict'
import { test } from 'node:test'
import { architectureNodes, questionForComponent } from '../architecture.ts'
import type { ChatMessage } from './conversation.ts'
import { selectionAnswer, selectionQuestions, WAITING_FOR_ANSWER } from './selection.ts'

const at = 1_700_000_000_000
const user = (id: string, content: string): ChatMessage => ({ id, role: 'user', content, createdAt: at })
const reply = (id: string, content: string, state: ChatMessage['state']): ChatMessage => ({ id, role: 'assistant', content, state, createdAt: at })
const Q = 'Tell me about JobPilot'

test('nothing selected shows nothing', () => {
  assert.equal(selectionAnswer([], null), null)
})

test('the selection\'s question not asked yet (queued behind another answer) waits', () => {
  assert.equal(selectionAnswer([user('u1', 'Other'), reply('a1', 'x', 'pending')], Q), WAITING_FOR_ANSWER)
})

test('the latest question\'s reply is shown: empty while it starts, then its text', () => {
  assert.equal(selectionAnswer([user('u1', Q), reply('a1', '', 'pending')], Q), '')
  assert.equal(selectionAnswer([user('u1', Q), reply('a1', 'It tailors applications.', 'done')], Q), 'It tailors applications.')
})

test('a newer question since then means the selection waits again', () => {
  const messages = [user('u1', Q), reply('a1', 'Done.', 'done'), user('u2', 'Newer'), reply('a2', '', 'pending')]
  assert.equal(selectionAnswer(messages, Q), WAITING_FOR_ANSWER)
})

test('after a failure the friendly failure reply is shown', () => {
  const messages = [user('u1', Q), reply('a1', 'Partial', 'error'), reply('a2', 'The backend tripped.', 'error')]
  assert.equal(selectionAnswer(messages, Q), 'The backend tripped.')
})

test('selection questions (sent and retried without history) cover components and projects only', () => {
  const set = selectionQuestions(['JobPilot', 'Ledger'])
  assert.ok(set.has(Q))
  assert.ok(set.has('Tell me about Ledger'))
  assert.ok(set.has(questionForComponent(architectureNodes[0].id)))
  assert.equal(set.has('What can you build for me?'), false)
})
