import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ANSWER_STOPPED_ANNOUNCEMENT, chatAnnouncement, RETRY_AVAILABLE_ANNOUNCEMENT, type AnnouncementState } from './chatAnnouncement.ts'
import type { ChatMessage } from './conversation.ts'

const quiet: AnnouncementState = { isStreaming: false, silencedReplyId: null, retryReadyReplyId: null }
const question: ChatMessage = { id: 'u1', role: 'user', content: 'What is Glassbox?', createdAt: 1 }

test('a settled answer is announced once streaming ends, never while it streams', () => {
  const messages: ChatMessage[] = [question, { id: 'a1', role: 'assistant', content: 'A RAG demo.', state: 'done', createdAt: 1 }]
  assert.equal(chatAnnouncement(messages, quiet), 'A RAG demo.')
  assert.equal(chatAnnouncement(messages, { ...quiet, isStreaming: true }), '')
})

test('Stop announces "Answer stopped." instead of the partial text, also before the first token', () => {
  const partial: ChatMessage[] = [question, { id: 'a1', role: 'assistant', content: 'A RAG', state: 'stopped', createdAt: 1 }]
  assert.equal(chatAnnouncement(partial, quiet), ANSWER_STOPPED_ANNOUNCEMENT)
  const empty: ChatMessage[] = [question, { id: 'a1', role: 'assistant', content: '', state: 'stopped', createdAt: 1 }]
  assert.equal(chatAnnouncement(empty, quiet), ANSWER_STOPPED_ANNOUNCEMENT)
})

test('stop-and-send never announces the interrupted reply, stopped or finished', () => {
  const stopped: ChatMessage[] = [question, { id: 'a1', role: 'assistant', content: 'A RAG', state: 'stopped', createdAt: 1 }]
  assert.equal(chatAnnouncement(stopped, { ...quiet, silencedReplyId: 'a1' }), '')
  const finished: ChatMessage[] = [question, { id: 'a1', role: 'assistant', content: 'A RAG demo.', state: 'done', createdAt: 1 }]
  assert.equal(chatAnnouncement(finished, { ...quiet, silencedReplyId: 'a1' }), '')
  // A later reply is announced as usual.
  const next: ChatMessage[] = [...stopped, { id: 'u2', role: 'user', content: 'More?', createdAt: 2 }, { id: 'a2', role: 'assistant', content: 'Sure.', state: 'done', createdAt: 2 }]
  assert.equal(chatAnnouncement(next, { ...quiet, silencedReplyId: 'a1' }), 'Sure.')
})

test('a failure reply is announced, then "Retry is available now." when its countdown ends', () => {
  const failed: ChatMessage[] = [question, { id: 'e1', role: 'assistant', content: 'Too many questions, try again in 30s.', state: 'error', retryAt: 5, createdAt: 1 }]
  assert.equal(chatAnnouncement(failed, quiet), 'Too many questions, try again in 30s.')
  assert.equal(chatAnnouncement(failed, { ...quiet, retryReadyReplyId: 'e1' }), RETRY_AVAILABLE_ANNOUNCEMENT)
  // Only for the reply whose countdown ended.
  assert.equal(chatAnnouncement(failed, { ...quiet, retryReadyReplyId: 'e0' }), 'Too many questions, try again in 30s.')
})

test('nothing is announced for an empty conversation or a question without a reply', () => {
  assert.equal(chatAnnouncement([], quiet), '')
  assert.equal(chatAnnouncement([question], quiet), '')
})
