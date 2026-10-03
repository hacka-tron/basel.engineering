import assert from 'node:assert/strict'
import { test } from 'node:test'
import { BUDGET_REPLIES, SYSTEM_RETRIEVAL_ONLY_REPLY, pickBudgetReply, retrievalOnlyReply } from './budgetReplies.ts'

test('there are 20 distinct, short replies', () => {
  assert.equal(BUDGET_REPLIES.length, 20)
  assert.equal(new Set(BUDGET_REPLIES).size, 20)
  for (const reply of BUDGET_REPLIES) assert.ok(reply.length <= 140, `${reply.length}: ${reply}`)
})

test("the owner's example is in, emoticon and all", () => {
  assert.ok(BUDGET_REPLIES.some((reply) => reply.startsWith('Basel ran out of money to pay for tokens D:')))
})

test('no line makes a firm return promise', () => {
  for (const reply of BUDGET_REPLIES) assert.doesNotMatch(reply, /\b(back|return|returns|ready) (tomorrow|at|by|in)\b(?!-ish)|\bwill be back\b/i, reply)
})

test('no reply points to a sources list (none is shown)', () => {
  for (const reply of BUDGET_REPLIES) assert.doesNotMatch(reply, /\b(sources?|below)\b/i, reply)
})

test('retrieval_only: About This System points to the chunk list; the others get a playful reply', () => {
  assert.equal(retrievalOnlyReply('system', null, () => 0), SYSTEM_RETRIEVAL_ONLY_REPLY)
  assert.match(SYSTEM_RETRIEVAL_ONLY_REPLY, /Retrieved chunks/)
  for (const topic of ['basel', 'portfolio'] as const) {
    assert.equal(retrievalOnlyReply(topic, null, () => 0), BUDGET_REPLIES[0])
    assert.ok(BUDGET_REPLIES.includes(retrievalOnlyReply(topic)))
  }
})

test('no precise reset times or amounts, and no blaming the visitor', () => {
  for (const reply of BUDGET_REPLIES) {
    assert.doesNotMatch(reply, /[$€£]|\d|\b(hours?|minutes?|midnight|UTC)\b/i, reply)
    assert.doesNotMatch(reply, /\b(you asked|your fault|too many questions|you used|you broke)\b/i, reply)
  }
})

test('some replies playfully blame Basel and some do not', () => {
  const basel = BUDGET_REPLIES.filter((reply) => reply.includes('Basel')).length
  assert.ok(basel >= 6 && basel <= 16, `${basel} mention Basel`)
})

test('picks come from the list and use the random source', () => {
  assert.equal(pickBudgetReply(null, () => 0), BUDGET_REPLIES[0])
  assert.equal(pickBudgetReply(null, () => 0.999999), BUDGET_REPLIES[19])
})

test('never repeats the avoided replies, and varies over many picks', () => {
  let previous: string | null = null
  const seen = new Set<string>()
  for (let i = 0; i < 500; i++) {
    const reply = pickBudgetReply(previous)
    assert.notEqual(reply, previous)
    previous = reply
    seen.add(reply)
  }
  assert.ok(seen.size >= 15, `only ${seen.size} distinct replies`)
  for (let i = 0; i < 200; i++) {
    const reply = pickBudgetReply([BUDGET_REPLIES[0], BUDGET_REPLIES[1], null, undefined])
    assert.ok(reply !== BUDGET_REPLIES[0] && reply !== BUDGET_REPLIES[1])
  }
  assert.ok(BUDGET_REPLIES.includes(pickBudgetReply([...BUDGET_REPLIES])))
})
