import assert from 'node:assert/strict'
import { test } from 'node:test'
import { CANONICAL_IDK, IDK_REPLIES, pickIdkReply } from './idkReplies.ts'

test('there are 20 distinct, short replies', () => {
  assert.equal(IDK_REPLIES.length, 20)
  assert.equal(new Set(IDK_REPLIES).size, 20)
  for (const reply of IDK_REPLIES) assert.ok(reply.length <= 120, reply)
})

test('picks come from the list and use the random source', () => {
  assert.equal(pickIdkReply(null, () => 0), IDK_REPLIES[0])
  assert.equal(pickIdkReply(null, () => 0.999999), IDK_REPLIES[19])
})

test('never repeats the avoided replies', () => {
  let previous: string | null = null
  for (let i = 0; i < 500; i++) {
    const reply = pickIdkReply(previous)
    assert.notEqual(reply, previous)
    previous = reply
  }
  for (let i = 0; i < 200; i++) {
    const reply = pickIdkReply([IDK_REPLIES[0], IDK_REPLIES[1], null, undefined])
    assert.ok(reply !== IDK_REPLIES[0] && reply !== IDK_REPLIES[1])
  }
  assert.notEqual(pickIdkReply(IDK_REPLIES[0], () => 0), IDK_REPLIES[0])
  assert.ok(IDK_REPLIES.includes(pickIdkReply([...IDK_REPLIES])))
})

test('a variety of replies is used over many picks', () => {
  const seen = new Set<string>()
  let previous: string | null = null
  for (let i = 0; i < 400; i++) {
    previous = pickIdkReply(previous)
    seen.add(previous)
  }
  assert.ok(seen.size >= 15, `only ${seen.size} distinct replies`)
})

test('no reply blames the visitor, insults Basel, or claims a fact', () => {
  const blame = /\b(you asked|you've asked|your fault|you should have|you didn't|you broke|you sent|stupid|useless|lazy|incompetent|hate)\b/i
  for (const reply of IDK_REPLIES) assert.doesNotMatch(reply, blame, reply)
})

test('some replies playfully mention Basel and some do not', () => {
  const about = IDK_REPLIES.filter((reply) => reply.includes('Basel')).length
  assert.ok(about >= 10 && about <= 17, `${about} mention Basel`)
})

test('the canonical sentence matches the server abstention', () => {
  assert.equal(CANONICAL_IDK, "I don't know from what I have.")
})
