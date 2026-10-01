// Per-corpus conversation memory (DESIGN-002 §5.1, §5.5). The server is
// stateless: the browser keeps each corpus's conversation, persists it in
// localStorage, and sends recent turns as `history` with each question.

import { CANONICAL_IDK } from './idkReplies.ts'

export type ApiCorpus = 'about_me' | 'about_system'

export type MessageSource = { source_path: string; title: string; url?: string }

export type SettledState = 'done' | 'stopped' | 'retrieval_only'

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  // `pending` covers thinking/streaming and is never saved. `error` marks a
  // friendly failure reply (lib/errorReplies.ts) or text cut off by a failure:
  // saved so a reload shows the same chat, but never sent back as history.
  state?: 'pending' | 'error' | SettledState
  sources?: MessageSource[]
  // The shown text is a playful stand-in for the server's abstention (lib/idkReplies.ts).
  // History sends the canonical sentence instead. Optional, so older saves load unchanged.
  idk?: boolean
  // Live-only: shows what a follow-up searched for. Not persisted (§5.5 shape).
  rewrittenQuery?: string
  createdAt: number
}

export type HistoryTurn = { role: 'user' | 'assistant'; content: string }

type StoredConversation = {
  version: 1
  updatedAt: number
  messages: {
    id: string
    role: 'user' | 'assistant'
    content: string
    state?: StoredState
    sources?: MessageSource[]
    idk?: boolean
    createdAt: number
  }[]
}

type StoredState = SettledState | 'error'

export const MAX_DISPLAY_MESSAGES = 50
export const MAX_HISTORY_MESSAGES = 6
// Matches the server's per-message and total limits (api/ask.py).
const MAX_HISTORY_CHARS = 4000
const EXPIRY_MS = 7 * 24 * 60 * 60 * 1000
// Tolerates small clock adjustments; anything further ahead is corrupt or skewed
// and would otherwise dodge the expiry indefinitely.
const MAX_FUTURE_SKEW_MS = 5 * 60 * 1000
const SETTLED_STATES: ReadonlySet<string> = new Set(['done', 'stopped', 'retrieval_only'])
// Saved to storage: settled answers plus failure replies.
const STORED_STATES: ReadonlySet<string> = new Set([...SETTLED_STATES, 'error'])

export function storageKey(corpus: ApiCorpus): string {
  return `glassbox:conv:v1:${corpus}`
}

export function newMessageId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  } catch { /* Fall through to the non-crypto id. */ }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

function isStorable(message: ChatMessage): boolean {
  // A reply stopped before its first token has no text but is still settled, so
  // a reload shows the same "Stopped" state the visitor saw.
  if (!message.content && !(message.role === 'assistant' && message.state === 'stopped')) return false
  return message.role === 'user' || (message.state !== undefined && STORED_STATES.has(message.state))
}

/** A turn the server may see as history: never an error reply or empty text. */
function isHistoryTurn(message: ChatMessage): boolean {
  if (!message.content) return false
  return message.role === 'user' || (message.state !== undefined && SETTLED_STATES.has(message.state))
}

function isSource(value: unknown): value is MessageSource {
  if (!value || typeof value !== 'object') return false
  const source = value as Record<string, unknown>
  return typeof source.source_path === 'string' && typeof source.title === 'string'
    && (source.url === undefined || typeof source.url === 'string')
}

function parseStored(raw: string, now: number): ChatMessage[] | null {
  const data: unknown = JSON.parse(raw)
  if (!data || typeof data !== 'object') return null
  const stored = data as Record<string, unknown>
  if (stored.version !== 1 || typeof stored.updatedAt !== 'number' || !Array.isArray(stored.messages)) return null
  if (now - stored.updatedAt > EXPIRY_MS) return null
  if (stored.updatedAt - now > MAX_FUTURE_SKEW_MS) return null
  const messages: ChatMessage[] = []
  for (const item of stored.messages as unknown[]) {
    if (!item || typeof item !== 'object') return null
    const message = item as Record<string, unknown>
    if (typeof message.id !== 'string' || typeof message.content !== 'string' || typeof message.createdAt !== 'number') return null
    if (message.role !== 'user' && message.role !== 'assistant') return null
    if (message.state !== undefined && (typeof message.state !== 'string' || !STORED_STATES.has(message.state))) return null
    if (message.sources !== undefined && (!Array.isArray(message.sources) || !message.sources.every(isSource))) return null
    if (message.idk !== undefined && typeof message.idk !== 'boolean') return null
    messages.push({
      id: message.id,
      role: message.role,
      content: message.content,
      state: message.state as StoredState | undefined,
      sources: message.sources as MessageSource[] | undefined,
      ...(message.idk === true ? { idk: true } : {}),
      createdAt: message.createdAt,
    })
  }
  return messages.slice(-MAX_DISPLAY_MESSAGES)
}

/** Restore a corpus's conversation; any storage or validation failure yields an empty one. */
export function loadConversation(corpus: ApiCorpus, now = Date.now()): ChatMessage[] {
  try {
    const raw = window.localStorage.getItem(storageKey(corpus))
    if (!raw) return []
    let messages: ChatMessage[] | null
    try {
      messages = parseStored(raw, now)
    } catch {
      messages = null
    }
    if (messages === null) {
      // Expired or unreadable data: start fresh rather than crash on it.
      window.localStorage.removeItem(storageKey(corpus))
      return []
    }
    return messages
  } catch {
    return []
  }
}

/**
 * The persisted form of a conversation: users, assistants that reached
 * done/stopped/retrieval_only, and error replies (marked `error`). Pending
 * messages are never saved, so a refresh mid-answer never restores a
 * half-written reply as if it were complete.
 */
export function serializeConversation(messages: ChatMessage[], now = Date.now()): string | null {
  const settled = messages.filter(isStorable).slice(-MAX_DISPLAY_MESSAGES)
  if (settled.length === 0) return null
  const stored: StoredConversation = {
    version: 1,
    updatedAt: now,
    messages: settled.map(({ id, role, content, state, sources, idk, createdAt }) => ({
      id,
      role,
      content,
      ...(role === 'assistant' && state && STORED_STATES.has(state) ? { state: state as StoredState } : {}),
      ...(sources && sources.length > 0 ? { sources } : {}),
      ...(role === 'assistant' && idk ? { idk: true } : {}),
      createdAt,
    })),
  }
  return JSON.stringify(stored)
}

export function writeConversation(corpus: ApiCorpus, serialized: string | null): void {
  try {
    if (serialized === null) window.localStorage.removeItem(storageKey(corpus))
    else window.localStorage.setItem(storageKey(corpus), serialized)
  } catch {
    // Storage full, blocked, or unavailable: keep working in memory only.
  }
}

/** The recent settled turns sent as `history` (the server re-applies its own limits). */
export function historyForRequest(messages: ChatMessage[]): HistoryTurn[] {
  const turns = messages.filter(isHistoryTurn).slice(-MAX_HISTORY_MESSAGES)
    .map((message) => ({
      role: message.role,
      // A quirky stand-in goes back as the sentence the server actually said.
      content: (message.idk ? CANONICAL_IDK : message.content).slice(0, MAX_HISTORY_CHARS),
    }))
  // Mirror the server's total-character cap, dropping the oldest turns first.
  let total = 0
  let start = turns.length
  while (start > 0 && total + turns[start - 1].content.length <= MAX_HISTORY_CHARS) {
    start -= 1
    total += turns[start].content.length
  }
  return turns.slice(start)
}
