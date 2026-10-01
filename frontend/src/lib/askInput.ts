// Ask box keyboard behaviour (DESIGN-002 §6.4): Up arrow in an empty input
// recalls the last question sent in this topic's conversation. Kept free of
// React so it can be unit tested with Node's built-in runner.

import type { ChatMessage } from './conversation.ts'

export type ArrowUpContext = {
  key: string
  value: string
  /** Caret position (selectionStart); null when the browser can't say. */
  caret: number | null
  /** IME composition in progress (`isComposing`, or the legacy keyCode 229). */
  composing: boolean
  /** Any of Shift, Alt, Ctrl or Meta held. */
  modified: boolean
}

/**
 * True if the caret sits on the first line of `value`. Always true for today's
 * single-line <input>; kept so a future multi-line textarea needs no change.
 */
export function caretOnFirstLine(value: string, caret: number | null): boolean {
  if (caret === null) return !value.includes('\n')
  return !value.slice(0, caret).includes('\n')
}

/**
 * Whether this keydown should recall the last question. Only a bare Up arrow
 * in an empty input, with the caret on the first line and no IME composition
 * in progress (Up there moves between the composition's candidates).
 */
export function shouldRecallQuestion(context: ArrowUpContext): boolean {
  if (context.key !== 'ArrowUp' || context.composing || context.modified) return false
  if (context.value !== '') return false
  return caretOnFirstLine(context.value, context.caret)
}

/** The last question the visitor sent in this conversation, or null. */
export function lastSentQuestion(messages: readonly ChatMessage[]): string | null {
  return messages.findLast((message) => message.role === 'user' && message.content.trim() !== '')?.content ?? null
}

export type AskButtonMode = 'send' | 'stop' | 'stop-and-send'

/**
 * What the button beside the ask box does. While an answer streams it is Stop
 * when the box is empty and "stop and send" once there is something to send,
 * so sending a new question (which stops the current answer first) is one tap.
 */
export function askButtonMode(isStreaming: boolean, value: string): AskButtonMode {
  if (!isStreaming) return 'send'
  return value.trim() ? 'stop-and-send' : 'stop'
}
