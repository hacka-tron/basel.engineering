import assert from 'node:assert/strict'
import { test } from 'node:test'
import { holdSaveDuringRetry, planRetry, retryableReplyId, retryWaitSeconds, withoutFailedAttempt } from './chatRetry.ts'
import { historyForRequest, type ChatMessage } from './conversation.ts'
import { BUDGET_REPLIES, LEGACY_BUDGET_ERROR_REPLY } from './budgetReplies.ts'

const settled: ChatMessage[] = [
  { id: 'u1', role: 'user', content: 'What did Basel build?', createdAt: 1 },
  { id: 'a1', role: 'assistant', content: 'Glassbox.', state: 'done', createdAt: 1 },
]
const failed: ChatMessage[] = [
  ...settled,
  { id: 'u2', role: 'user', content: 'Tell me more', createdAt: 2 },
  { id: 'e2', role: 'assistant', content: 'The backend tripped.', state: 'error', createdAt: 2 },
]

test('the latest failure reply is retryable and re-asks its question', () => {
  const plan = planRetry(failed)
  assert.deepEqual(plan, { question: 'Tell me more', userMessageId: 'u2', before: settled })
  assert.equal(retryableReplyId(failed), 'e2')
})

test('a failure that cut off a partial answer retries past both error messages', () => {
  const messages: ChatMessage[] = [
    ...settled,
    { id: 'u2', role: 'user', content: 'Tell me more', createdAt: 2 },
    { id: 'p2', role: 'assistant', content: 'Half an ans', state: 'error', createdAt: 2 },
    { id: 'e2', role: 'assistant', content: 'Connection dropped.', state: 'error', createdAt: 2 },
  ]
  assert.equal(planRetry(messages)?.userMessageId, 'u2')
  assert.deepEqual(withoutFailedAttempt(messages, 'u2').map((m) => m.id), ['u1', 'a1', 'u2'])
})

test('retry history is what the failed request sent: no duplicated question, no error text', () => {
  const plan = planRetry(failed)!
  const history = historyForRequest(plan.before)
  assert.deepEqual(history, [
    { role: 'user', content: 'What did Basel build?' },
    { role: 'assistant', content: 'Glassbox.' },
  ])
  // A first question that failed retries with no history, so it stays answer-cache eligible.
  assert.deepEqual(historyForRequest(planRetry(failed.slice(2))!.before), [])
})

test('only the latest message can be retried', () => {
  const movedOn: ChatMessage[] = [
    ...failed,
    { id: 'u3', role: 'user', content: 'Another', createdAt: 3 },
    { id: 'a3', role: 'assistant', content: 'Sure.', state: 'done', createdAt: 3 },
  ]
  assert.equal(planRetry(movedOn), null)
  assert.equal(retryableReplyId(movedOn), null)
  assert.equal(planRetry(settled), null)
  assert.equal(planRetry([]), null)
})

test('pending, stopped and budget replies are not retryable', () => {
  assert.equal(planRetry([...failed.slice(0, 3), { id: 'p', role: 'assistant', content: '', state: 'pending', createdAt: 2 }]), null)
  assert.equal(planRetry([...failed.slice(0, 3), { id: 's', role: 'assistant', content: 'Part', state: 'stopped', createdAt: 2 }]), null)
  // Budget replies are recognized by their flag, whichever random text they show.
  for (const content of BUDGET_REPLIES) {
    const messages: ChatMessage[] = [...failed.slice(0, 3), { id: 'b', role: 'assistant', content, state: 'error', budget: true, createdAt: 2 }]
    assert.equal(planRetry(messages), null)
    assert.equal(retryableReplyId(messages), null)
  }
})

test('a generic failure that happens to read like a budget reply is still retryable', () => {
  assert.equal(planRetry([...failed.slice(0, 3), { id: 'e', role: 'assistant', content: BUDGET_REPLIES[0], state: 'error', createdAt: 2 }])?.userMessageId, 'u2')
  assert.equal(planRetry([...failed.slice(0, 3), { id: 'e', role: 'assistant', content: LEGACY_BUDGET_ERROR_REPLY, state: 'error', createdAt: 2 }])?.userMessageId, 'u2')
})

test('a failure whose question was trimmed off the display cap is not retryable', () => {
  assert.equal(planRetry([{ id: 'e', role: 'assistant', content: 'Oops', state: 'error', createdAt: 1 }]), null)
})

test('withoutFailedAttempt leaves the conversation alone if the question is gone', () => {
  assert.deepEqual(withoutFailedAttempt(settled, 'missing'), settled)
})

test('retryWaitSeconds counts down to zero', () => {
  assert.equal(retryWaitSeconds(undefined, 1000), 0)
  assert.equal(retryWaitSeconds(11_000, 1000), 10)
  assert.equal(retryWaitSeconds(1_500, 1000), 1)
  assert.equal(retryWaitSeconds(1000, 1000), 0)
  assert.equal(retryWaitSeconds(500, 1000), 0)
})

test('a pending retry holds the saved conversation (with its failure reply) until the new reply settles', () => {
  const plan = planRetry(failed)!
  const retrying: ChatMessage[] = [
    ...withoutFailedAttempt(failed, plan.userMessageId),
    { id: 'r2', role: 'assistant', content: 'Partial', state: 'pending', createdAt: 3 },
  ]
  assert.equal(holdSaveDuringRetry(retrying, 'r2'), true)
  // Settled (done, stopped, or failed again and replaced by a new error reply): save.
  assert.equal(holdSaveDuringRetry(retrying.map((m) => m.id === 'r2' ? { ...m, state: 'done' as const } : m), 'r2'), false)
  assert.equal(holdSaveDuringRetry(retrying.filter((m) => m.id !== 'r2'), 'r2'), false)
  // Not a retry: a normal ask saves its question straight away.
  assert.equal(holdSaveDuringRetry(retrying, null), false)
})
