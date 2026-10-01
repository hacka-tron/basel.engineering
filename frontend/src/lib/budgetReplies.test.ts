import assert from 'node:assert/strict'
import { test } from 'node:test'
import { BUDGET_REPLIES, CANONICAL_BUDGET_REPLY, pickBudgetReply } from './budgetReplies.ts'

test('there are 20 distinct, short replies', () => {
  assert.equal(BUDGET_REPLIES.length, 20)
  assert.equal(new Set(BUDGET_REPLIES).size, 20)
  for (const reply of BUDGET_REPLIES) assert.ok(reply.length <= 140, `${reply.length}: ${reply}`)
  assert.ok(!BUDGET_REPLIES.includes(CANONICAL_BUDGET_REPLY))
})

test("the owner's example is in, emoticon and all", () => {
  assert.ok(BUDGET_REPLIES.some((reply) => reply.startsWith('Basel ran out of money to pay for tokens D:')))
})

test('every reply points to the sources and says answers come back later', () => {
  for (const reply of BUDGET_REPLIES) {
    assert.match(reply, /\bsources\b/i, reply)
    assert.match(reply, /\b(tomorrow|later|eventually|soon)\b/i, reply)
  }
})

test('no reply names an amount or a precise reset time, or blames the visitor', () => {
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
