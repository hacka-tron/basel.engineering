// What the chat's polite live region says (DESIGN-002 §6.5). It holds one short
// text that changes once per settled event, never per streamed token:
// - nothing while an answer streams;
// - the settled reply (answer, or friendly failure reply) once it settles;
// - "Answer stopped." after Stop, instead of re-reading the partial text;
// - nothing for an answer stopped (or finished) by sending a new question,
//   since the new question starts streaming straight away;
// - "Retry is available now." when a rate-limit countdown on Retry ends.
// Kept free of React so it can be unit tested with Node's built-in runner.

import type { ChatMessage } from './conversation.ts'

export const ANSWER_STOPPED_ANNOUNCEMENT = 'Answer stopped.'
export const RETRY_AVAILABLE_ANNOUNCEMENT = 'Retry is available now.'

export type AnnouncementState = {
  isStreaming: boolean
  /** The reply that a stop-and-send interrupted; it is never announced. */
  silencedReplyId: string | null
  /** The failure reply whose Retry countdown has just ended. */
  retryReadyReplyId: string | null
}

export function chatAnnouncement(messages: readonly ChatMessage[], { isStreaming, silencedReplyId, retryReadyReplyId }: AnnouncementState): string {
  if (isStreaming) return ''
  const last = messages[messages.length - 1]
  if (!last || last.role !== 'assistant' || last.id === silencedReplyId) return ''
  if (last.state === 'stopped') return ANSWER_STOPPED_ANNOUNCEMENT
  if (last.state === 'error' && last.id === retryReadyReplyId) return RETRY_AVAILABLE_ANNOUNCEMENT
  return last.content
}
