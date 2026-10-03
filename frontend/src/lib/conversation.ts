// Per-corpus conversation memory (DESIGN-002 §5.1, §5.5). The server is
// stateless: the browser keeps each corpus's conversation, persists it in
// localStorage, and sends recent turns as `history` with each question.

import { LEGACY_BUDGET_ERROR_REPLY } from './budgetReplies.ts'
import { CANONICAL_IDK } from './idkReplies.ts'

export type ApiCorpus = 'about_me' | 'about_system' | 'portfolio'

export type SettledState = 'done' | 'stopped' | 'retrieval_only'

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  // `pending` covers thinking/streaming and is never saved. `error` marks a
  // friendly failure reply (lib/errorReplies.ts) or text cut off by a failure:
  // saved so a reload shows the same chat, but never sent back as history.
  state?: 'pending' | 'error' | SettledState
  // The shown text is a playful stand-in for the server's abstention (lib/idkReplies.ts).
  // History sends the canonical sentence instead. Optional, so older saves load unchanged.
  idk?: boolean
  // The shown text is a playful daily-budget reply (lib/budgetReplies.ts): on a
  // retrieval_only answer, or a `budget_exhausted` failure (which Retry skips).
  // History leaves the whole turn out (isHistoryTurn). Optional, as for `idk`.
  budget?: boolean
  // Live-only: shows what a follow-up searched for. Not persisted (§5.5 shape).
  rewrittenQuery?: string
  // Live-only, on a rate-limited failure reply: when Retry may be pressed again
  // (epoch ms, from the server's retry-after). Not persisted.
  retryAt?: number
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
    idk?: boolean
    budget?: boolean
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
// What history may carry: a retrieval_only (budget-exhausted) turn is left out,
// question and all, because the server never said anything for it.
const HISTORY_STATES: ReadonlySet<string> = new Set(['done', 'stopped'])
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

/**
 * A turn the server may see as history: never an error reply or empty text,
 * and a question only if its reply settled (done or stopped). A question that
 * failed, or has no reply yet, is left out, so re-sending it (Retry, or
 * Up-arrow and Enter) never puts the same question in twice. A stopped reply
 * with no text (after trim) is left out whole, like a failed turn. A retrieval_only
 * (budget-exhausted) turn is left out whole, question and reply: the server said
 * nothing for it, and dropping both avoids adding a lone user turn.
 */
function isHistoryTurn(message: ChatMessage, next: ChatMessage | undefined): boolean {
  if (!message.content) return false
  if (message.role === 'user') {
    // A stopped reply with no text (blank after trim) leaves the question out too,
    // like a failed turn, so no lone user turn lands in history.
    return next?.role === 'assistant' && next.state !== undefined && HISTORY_STATES.has(next.state)
      && !(next.state === 'stopped' && !next.content.trim())
  }
  return message.state !== undefined && HISTORY_STATES.has(message.state)
    && !(message.state === 'stopped' && !message.content.trim())
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
    if (message.idk !== undefined && typeof message.idk !== 'boolean') return null
    if (message.budget !== undefined && typeof message.budget !== 'boolean') return null
    // Only assistant replies carry these flags; a user message never does.
    // Saves from before the budget flag carry the one fixed budget failure reply.
    const assistant = message.role === 'assistant'
    const budget = assistant && (message.budget === true
      || (message.state === 'error' && message.content === LEGACY_BUDGET_ERROR_REPLY))
    messages.push({
      id: message.id,
      role: message.role,
      content: message.content,
      state: message.state as StoredState | undefined,
      ...(assistant && message.idk === true ? { idk: true } : {}),
      ...(budget ? { budget: true } : {}),
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
    messages: settled.map(({ id, role, content, state, idk, budget, createdAt }) => ({
      id,
      role,
      content,
      ...(role === 'assistant' && state && STORED_STATES.has(state) ? { state: state as StoredState } : {}),
      ...(role === 'assistant' && idk ? { idk: true } : {}),
      ...(role === 'assistant' && budget ? { budget: true } : {}),
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

function canonicalContent(message: ChatMessage): string {
  if (message.idk) return CANONICAL_IDK
  return message.content
}

/** The recent settled turns sent as `history` (the server re-applies its own limits). */
export function historyForRequest(messages: ChatMessage[]): HistoryTurn[] {
  const turns = messages.filter((message, index) => isHistoryTurn(message, messages[index + 1])).slice(-MAX_HISTORY_MESSAGES)
    .map((message) => ({
      role: message.role,
      // A quirky stand-in goes back as its canonical sentence, never the joke.
      content: canonicalContent(message).slice(0, MAX_HISTORY_CHARS),
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

/**
 * True when `question` is the conversation's latest question and its reply
 * finished normally (`done` or `retrieval_only`). Re-selecting a diagram
 * component then just shows that reply instead of asking again, which would
 * append a duplicate turn and spend one of the visitor's rate-limited
 * questions (the limiter runs before the answer cache). A failed reply
 * (Retry exists for it), a stopped one, or one still streaming returns false.
 */
export function latestQuestionAnswered(messages: readonly ChatMessage[], question: string): boolean {
  const questionIndex = messages.findLastIndex((message) => message.role === 'user')
  if (questionIndex === -1 || messages[questionIndex].content !== question) return false
  const reply = messages.slice(questionIndex + 1).findLast((message) => message.role === 'assistant')
  return reply?.state === 'done' || reply?.state === 'retrieval_only'
}
