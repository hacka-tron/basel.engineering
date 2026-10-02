// What a selected diagram component or portfolio project shows, and which
// questions are "selection questions" (asked, and retried, without history so
// they stay answer-cache eligible).
import { architectureNodes, questionForComponent } from '../architecture.ts'
import type { ChatMessage } from './conversation.ts'
import { questionForProject } from './portfolioView.ts'

export const WAITING_FOR_ANSWER = 'Waiting for the current answer…'

/**
 * The reply to the selection's question when that question is the topic's
 * latest one: its text ('' while it starts), or the friendly failure reply
 * that follows any partial text. Otherwise (queued behind another answer, or
 * a newer question since) WAITING_FOR_ANSWER. Null with nothing selected.
 */
export function selectionAnswer(messages: readonly ChatMessage[], question: string | null): string | null {
  if (question === null) return null
  const questionIndex = messages.findLastIndex((message) => message.role === 'user' && message.content === question)
  if (questionIndex === -1 || questionIndex !== messages.findLastIndex((message) => message.role === 'user')) return WAITING_FOR_ANSWER
  const reply = messages.slice(questionIndex + 1).findLast((message) => message.role === 'assistant')
  return reply ? reply.content : WAITING_FOR_ANSWER
}

export function selectionQuestions(projectTitles: readonly string[]): ReadonlySet<string> {
  return new Set([
    ...architectureNodes.map((node) => questionForComponent(node.id)),
    ...projectTitles.map(questionForProject),
  ])
}
