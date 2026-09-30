import assert from 'node:assert/strict'
import { test } from 'node:test'
import { historyForRequest, loadConversation, serializeConversation, type ChatMessage } from './conversation.ts'

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

test('empty replies that are still pending or errored are never saved', () => {
  const serialized = serializeConversation([
    { id: 'u', role: 'user', content: 'q', createdAt: now },
    { id: 'p', role: 'assistant', content: '', state: 'pending', createdAt: now },
    { id: 'e', role: 'assistant', content: '', state: 'error', createdAt: now },
  ], now)
  assert.deepEqual(JSON.parse(serialized!).messages.map((message: { id: string }) => message.id), ['u'])
})
