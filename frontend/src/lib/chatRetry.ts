// Retry on a failure reply (DESIGN-002 §6.1 `error`, §6.4). Retry *replaces*
// the failed attempt: the error bubble(s) after the failed question are removed
// and the same question is asked again in place, so the conversation (and what
// is saved) reads as if the first attempt had worked. History for the retry is
// the conversation before the failed question, exactly what the failed request
// sent, so the question is never duplicated in the history sent to the API.
// Kept free of React so it can be unit tested with Node's built-in runner.

import type { ChatMessage } from './conversation.ts'

export type RetryPlan = {
  /** The failed user question, asked again as is. */
  question: string
  /** Its message id; the retry keeps this message and drops everything after it. */
  userMessageId: string
  /** The conversation before the failed question: the source of the retry's history. */
  before: ChatMessage[]
}

function isErrorReply(message: ChatMessage): boolean {
  return message.role === 'assistant' && message.state === 'error'
}

/**
 * The retry for a conversation whose latest message is a failure reply, or
 * null. Only the latest failure can be retried: retrying an older one would
 * splice a new answer into the middle of a conversation that has moved on.
 * A daily-budget reply (marked `budget`) is not retryable (it would fail the same way until
 * tomorrow), nor is a failure whose question was trimmed off the display cap.
 */
export function planRetry(messages: readonly ChatMessage[]): RetryPlan | null {
  const last = messages[messages.length - 1]
  if (!last || !isErrorReply(last) || last.budget) return null
  let index = messages.length - 1
  // Skip the reply and any partial answer the failure cut off (also `error`).
  while (index >= 0 && isErrorReply(messages[index])) index -= 1
  const user = messages[index]
  if (!user || user.role !== 'user' || !user.content) return null
  return { question: user.content, userMessageId: user.id, before: messages.slice(0, index) }
}

/** The id of the failure reply that shows Retry, or null. */
export function retryableReplyId(messages: readonly ChatMessage[]): string | null {
  return planRetry(messages) ? messages[messages.length - 1].id : null
}

/**
 * The conversation with the failed attempt removed: everything up to and
 * including the failed question stays, the failure replies after it go. If
 * the question is no longer there (e.g. New chat raced the click), it is
 * returned unchanged.
 */
export function withoutFailedAttempt(messages: readonly ChatMessage[], userMessageId: string): ChatMessage[] {
  const index = messages.findIndex((message) => message.id === userMessageId)
  if (index === -1) return [...messages]
  return messages.slice(0, index + 1)
}

/** Whole seconds until a rate-limited Retry may be pressed (0 once it may). */
export function retryWaitSeconds(retryAt: number | undefined, now: number): number {
  if (retryAt === undefined) return 0
  return Math.max(0, Math.ceil((retryAt - now) / 1000))
}

/**
 * Whether the conversation must keep its saved form for now: true while the
 * reply to an in-flight retry is still pending. Retry removes the failure
 * reply from the shown conversation at once, but the saved copy keeps it (and
 * so the Retry button) until the new answer settles, so a reload mid-retry
 * comes back to the failed question with a working Retry. Nothing new is
 * written in the meantime: the pending reply is never saved, as in a normal
 * mid-answer reload, and no error that did not happen is invented.
 */
export function holdSaveDuringRetry(messages: readonly ChatMessage[], retryReplyId: string | null): boolean {
  if (retryReplyId === null) return false
  return messages.some((message) => message.id === retryReplyId && message.state === 'pending')
}
