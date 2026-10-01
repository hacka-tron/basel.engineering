import assert from 'node:assert/strict'
import { test } from 'node:test'
import { historyForRequest, latestQuestionAnswered, loadConversation, serializeConversation, type ChatMessage } from './conversation.ts'

const now = 1_700_000_000_000
const messages: ChatMessage[] = [
  { id: 'u1', role: 'user', content: 'What did Basel build?', createdAt: now },
  { id: 'a1', role: 'assistant', content: 'A partial ans', state: 'stopped', createdAt: now },
  { id: 'u2', role: 'user', content: 'Stop this one early', createdAt: now },
  { id: 'a2', role: 'assistant', content: '', state: 'stopped', createdAt: now },
]

function withStorage(values: Record<string, string>, run: () => void) {
  const storage = {
    getItem: (key: string) => values[key] ?? null,
    setItem: (key: string, value: string) => { values[key] = value },
    removeItem: (key: string) => { delete values[key] },
  }
  const global = globalThis as { window?: unknown }
  const previous = global.window
  global.window = { localStorage: storage }
  try { run() } finally { global.window = previous }
}

test('a reply stopped before its first token survives a reload as stopped', () => {
  const serialized = serializeConversation(messages, now)
  assert.ok(serialized)
  withStorage({ 'glassbox:conv:v1:about_me': serialized }, () => {
    const restored = loadConversation('about_me', now)
    assert.deepEqual(restored.map((message) => [message.id, message.content, message.state]), [
      ['u1', 'What did Basel build?', undefined],
      ['a1', 'A partial ans', 'stopped'],
      ['u2', 'Stop this one early', undefined],
      ['a2', '', 'stopped'],
    ])
  })
})

test('an empty stopped reply is not sent as history, but partial stopped text is', () => {
  assert.deepEqual(historyForRequest(messages), [
    { role: 'user', content: 'What did Basel build?' },
    { role: 'assistant', content: 'A partial ans' },
    { role: 'user', content: 'Stop this one early' },
  ])
})

test('pending and empty errored replies are never saved', () => {
  const serialized = serializeConversation([
    { id: 'u', role: 'user', content: 'q', createdAt: now },
    { id: 'p', role: 'assistant', content: 'half an ans', state: 'pending', createdAt: now },
    { id: 'e', role: 'assistant', content: '', state: 'error', createdAt: now },
  ], now)
  assert.deepEqual(JSON.parse(serialized!).messages.map((message: { id: string }) => message.id), ['u'])
})

test('error replies are persisted as error but never sent as history', () => {
  const chat: ChatMessage[] = [
    { id: 'u1', role: 'user', content: 'First question', createdAt: now },
    { id: 'a1', role: 'assistant', content: 'A real answer.', state: 'done', createdAt: now },
    { id: 'u2', role: 'user', content: 'Second question', createdAt: now },
    { id: 'a2', role: 'assistant', content: 'Partial text cut', state: 'error', createdAt: now },
    { id: 'r2', role: 'assistant', content: 'Oops — something broke. Try again?', state: 'error', createdAt: now },
  ]
  const serialized = serializeConversation(chat, now)!
  withStorage({ 'glassbox:conv:v1:about_system': serialized }, () => {
    const restored = loadConversation('about_system', now)
    assert.deepEqual(restored.map((message) => [message.id, message.state]), [
      ['u1', undefined], ['a1', 'done'], ['u2', undefined], ['a2', 'error'], ['r2', 'error'],
    ])
    // The failed question is left out too, so re-sending it can't duplicate it.
    assert.deepEqual(historyForRequest(restored), [
      { role: 'user', content: 'First question' },
      { role: 'assistant', content: 'A real answer.' },
    ])
  })
})

test('re-sending a failed question (Up-arrow and Enter) never puts it in history twice', () => {
  const chat: ChatMessage[] = [
    { id: 'u1', role: 'user', content: 'First question', createdAt: now },
    { id: 'a1', role: 'assistant', content: 'A real answer.', state: 'done', createdAt: now },
    { id: 'u2', role: 'user', content: 'Tell me more', createdAt: now },
    { id: 'r2', role: 'assistant', content: 'Oops — something broke.', state: 'error', createdAt: now },
  ]
  // History for re-sending "Tell me more" is what the failed request sent.
  assert.deepEqual(historyForRequest(chat), [
    { role: 'user', content: 'First question' },
    { role: 'assistant', content: 'A real answer.' },
  ])
  // A question with no settled reply yet (still pending) is left out as well.
  assert.deepEqual(historyForRequest([...chat.slice(0, 3), { id: 'p', role: 'assistant', content: '', state: 'pending', createdAt: now }]), historyForRequest(chat))
})

test('a quirky "I don\'t know" reply is shown and saved, but history carries the canonical sentence', () => {
  const convo: ChatMessage[] = [
    { id: 'u1', role: 'user', content: 'What is his shoe size?', createdAt: now },
    { id: 'a1', role: 'assistant', content: 'Basel forgot to write that part down. Classic Basel.', state: 'done', idk: true, createdAt: now },
    { id: 'u2', role: 'user', content: 'ok what about projects', createdAt: now },
  ]
  // u2 has no reply yet, so it isn't history (it is the question being asked).
  assert.deepEqual(historyForRequest(convo), [
    { role: 'user', content: 'What is his shoe size?' },
    { role: 'assistant', content: "I don't know from what I have." },
  ])
  const serialized = serializeConversation(convo, now)!
  withStorage({ 'glassbox:conv:v1:about_me': serialized }, () => {
    const restored = loadConversation('about_me', now)
    assert.equal(restored[1].content, 'Basel forgot to write that part down. Classic Basel.')
    assert.equal(restored[1].idk, true)
    assert.equal(historyForRequest(restored)[1].content, "I don't know from what I have.")
  })
})

test('conversations saved before the idk flag existed still load', () => {
  const old = JSON.stringify({
    version: 1,
    updatedAt: now,
    messages: [
      { id: 'a', role: 'assistant', content: "I don't know from what I have.", state: 'done', createdAt: now },
    ],
  })
  withStorage({ 'glassbox:conv:v1:about_me': old }, () => {
    const restored = loadConversation('about_me', now)
    assert.equal(restored.length, 1)
    assert.equal(restored[0].idk, undefined)
    assert.equal(historyForRequest(restored)[0].content, "I don't know from what I have.")
  })
})

test('latestQuestionAnswered: a settled answer to the latest question means re-selecting does not ask again', () => {
  const q = 'How does the Queue component work?'
  const turn = (state: ChatMessage['state'], extra: ChatMessage[] = []): ChatMessage[] => [
    { id: 'u', role: 'user', content: q, createdAt: now },
    { id: 'a', role: 'assistant', content: 'Answer', state, createdAt: now },
    ...extra,
  ]
  assert.equal(latestQuestionAnswered(turn('done'), q), true)
  assert.equal(latestQuestionAnswered(turn('retrieval_only'), q), true)
  // Failed (Retry exists), stopped early, or still streaming: ask again.
  assert.equal(latestQuestionAnswered(turn('error'), q), false)
  assert.equal(latestQuestionAnswered(turn('stopped'), q), false)
  assert.equal(latestQuestionAnswered(turn('pending'), q), false)
  // Partial text cut off by a failure, followed by the friendly failure reply.
  assert.equal(latestQuestionAnswered(turn('error', [{ id: 'e', role: 'assistant', content: 'Oops', state: 'error', createdAt: now }]), q), false)
  // Another question came after it, or there is no conversation yet.
  assert.equal(latestQuestionAnswered([...turn('done'), { id: 'u2', role: 'user', content: 'Other', createdAt: now }, { id: 'a2', role: 'assistant', content: 'x', state: 'done', createdAt: now }], q), false)
  assert.equal(latestQuestionAnswered([], q), false)
  assert.equal(latestQuestionAnswered(turn('done'), 'A different question'), false)
})
