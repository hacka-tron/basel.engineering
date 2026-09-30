import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ERROR_REPLIES, errorReplyFor, pickErrorReply } from './errorReplies.ts'

test('there are 20 distinct, short replies', () => {
  assert.equal(ERROR_REPLIES.length, 20)
  assert.equal(new Set(ERROR_REPLIES).size, 20)
  for (const reply of ERROR_REPLIES) assert.ok(reply.length <= 100, reply)
})

test('picks come from the list and use the random source', () => {
  assert.equal(pickErrorReply(null, () => 0), ERROR_REPLIES[0])
  assert.equal(pickErrorReply(null, () => 0.999999), ERROR_REPLIES[19])
  for (let i = 0; i < 200; i++) assert.ok(ERROR_REPLIES.includes(pickErrorReply()))
})

test('never repeats the previous reply back to back', () => {
  let previous: string | null = null
  for (let i = 0; i < 500; i++) {
    const reply = pickErrorReply(previous)
    assert.notEqual(reply, previous)
    previous = reply
  }
  // Even when the random source would land on the previous reply.
  assert.notEqual(pickErrorReply(ERROR_REPLIES[0], () => 0), ERROR_REPLIES[0])
})

test('over many failures, a variety of replies is used', () => {
  const seen = new Set<string>()
  let previous: string | null = null
  for (let i = 0; i < 400; i++) {
    previous = pickErrorReply(previous)
    seen.add(previous)
  }
  assert.ok(seen.size >= 15, `only ${seen.size} distinct replies`)
})

test('rate limiting keeps the wait time', () => {
  assert.match(errorReplyFor({ code: 'rate_limited', retry_after_s: 37 }), /about 37 seconds/)
  assert.match(errorReplyFor({ code: 'rate_limited', retry_after_s: 1 }), /about 1 second\b/)
  assert.match(errorReplyFor({ code: 'rate_limited' }), /a moment/)
})

test('an exhausted daily budget says so', () => {
  assert.match(errorReplyFor({ code: 'budget_exhausted' }), /answer limit for today/)
})

test('no reply blames the visitor', () => {
  const replies = [
    ...ERROR_REPLIES,
    errorReplyFor({ code: 'rate_limited', retry_after_s: 37 }),
    errorReplyFor({ code: 'rate_limited' }),
    errorReplyFor({ code: 'budget_exhausted' }),
  ]
  const blame = /\b(you asked|you've asked|your fault|too many questions|slow down|lot of questions at once|you did|you broke|you sent)\b/i
  for (const reply of replies) assert.doesNotMatch(reply, blame, reply)
})

test('can avoid several replies at once, e.g. the one saved just above after a reload', () => {
  for (let i = 0; i < 200; i++) {
    const reply = pickErrorReply([ERROR_REPLIES[0], ERROR_REPLIES[1]])
    assert.ok(reply !== ERROR_REPLIES[0] && reply !== ERROR_REPLIES[1])
  }
  assert.ok(ERROR_REPLIES.includes(pickErrorReply([...ERROR_REPLIES])))
})

test('generic failures get a random reply', () => {
  assert.ok(ERROR_REPLIES.includes(errorReplyFor({ code: 'internal' })))
  assert.notEqual(errorReplyFor({ code: 'internal' }, ERROR_REPLIES[3], () => 0.15), ERROR_REPLIES[3])
})
