import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import type { Corpus } from '../App'
import type { ChatMessage } from '../lib/conversation'

const questions: Record<Corpus, string[]> = {
  basel: [
    'What has Basel built with distributed systems?',
    'What did Basel work on at YouTube?',
    'Is Basel a fit for a platform engineering role?',
  ],
  system: [
    'How does the caching work?',
    'Why k3s instead of EKS?',
    'What happens when I press stress test?',
    'Show me the Terraform for the database.',
  ],
}

type ChatProps = {
  corpus: Corpus
  messages: ChatMessage[]
  isStreaming: boolean
  onAsk: (question: string) => void
  onNewChat: () => void
  errorMessage: string | null
  inputAccessory?: ReactNode
}

function Chat({ corpus, messages, isStreaming, onAsk, onNewChat, errorMessage, inputAccessory }: ChatProps) {
  const [question, setQuestion] = useState('')
  const messagesRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const container = messagesRef.current
    if (container) container.scrollTop = container.scrollHeight
  }, [messages])

  // The message list mutates on every streamed token; a live region on the
  // whole list would re-announce (or re-read) on each mutation. Instead, a
  // separate off-screen live region holds the settled assistant text only —
  // it's empty for the whole duration of a stream (so no per-token
  // announcements) and becomes non-empty in a single mutation once
  // `isStreaming` flips false, which a screen reader announces once.
  const lastMessage = messages[messages.length - 1]
  const announcement = !isStreaming && lastMessage?.role === 'assistant' ? lastMessage.content : ''

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedQuestion = question.trim()
    if (!trimmedQuestion || isStreaming) return
    onAsk(trimmedQuestion)
    setQuestion('')
  }

  return (
    <section aria-label="Chat" className="flex min-h-0 min-w-0 flex-col bg-panel md:border-r md:border-hairline">
      <div aria-live="polite" className="sr-only">{announcement}</div>
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <div ref={messagesRef} aria-label="Messages" aria-live="off" className="min-h-0 flex-1 overflow-y-auto px-7 py-6">
          <div className="flex flex-col gap-4">
            {messages.map((message) => {
              const pending = message.state === 'pending'
              if (message.role === 'assistant' && !message.content && !pending) return null
              const showSources = message.role === 'assistant' && !pending && (message.sources?.length ?? 0) > 0
              return (
                <div key={message.id} className={`flex min-w-0 max-w-[90%] flex-col gap-1.5 ${message.role === 'user' ? 'self-end' : 'self-start'}`}>
                  <div
                    className={`whitespace-pre-wrap break-words rounded-[3px] border border-hairline px-4 py-3 text-sm leading-relaxed text-primary ${message.role === 'user' ? 'bg-canvas' : 'bg-panel'}`}
                  >
                    {message.content || '…'}
                  </div>
                  {showSources && (
                    <p className="break-words px-1 text-[10px] leading-relaxed text-muted">
                      <span>Sources: </span>
                      {message.sources!.map((source, index) => (
                        <span key={source.source_path}>
                          {index > 0 && ' · '}
                          {source.url ? (
                            <a href={source.url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-primary">{source.title}</a>
                          ) : (
                            <span title={source.source_path}>{source.title}</span>
                          )}
                        </span>
                      ))}
                    </p>
                  )}
                  {/* Follow-ups show the standalone query retrieval actually used (DESIGN-002 §5.2). */}
                  {message.rewrittenQuery && !pending && (
                    <p className="break-words px-1 text-[10px] leading-relaxed text-muted">Searched for: {message.rewrittenQuery}</p>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        {/* Suggested questions return whenever this tab's conversation is empty. */}
        {messages.length === 0 && <div className="shrink-0 px-7 pb-6">
          <p className="mb-3 text-xs text-muted">Suggested questions</p>
          {/*
            A wrapping grid of these chips can run to 3+ full rows on a
            narrow phone (long question text + a 4-row wrapped header above
            it), squeezing the actual message list — which has to share the
            same flex column — down to a sliver. Below `md`, this scrolls
            horizontally as a single row instead (matching PipelineStrip's
            existing pattern) so the conversation always keeps most of the
            vertical space; at `md`+ there's room to spare, so it wraps as
            before.
          */}
          <div className="flex gap-2 overflow-x-auto pb-1 md:flex-wrap md:overflow-visible md:pb-0">
            {questions[corpus].map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => setQuestion(suggestion)}
                className="shrink-0 whitespace-nowrap rounded-[3px] border border-hairline px-3 py-2 text-left text-xs leading-relaxed text-muted transition-colors hover:text-primary md:shrink md:whitespace-normal"
              >
                {suggestion}
              </button>
            ))}
          </div>
        </div>}
      </div>

      {inputAccessory}

      <div className="shrink-0 px-7 pb-7">
        {errorMessage && <p role="alert" className="mb-2 text-xs text-muted">{errorMessage}</p>}
        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            aria-label="Ask anything"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            disabled={isStreaming}
            placeholder="Ask anything..."
            className="min-w-0 flex-1 rounded-[3px] border border-hairline bg-canvas px-3 py-3 text-sm text-primary outline-none placeholder:text-muted focus:border-cyan disabled:cursor-not-allowed"
          />
          <button
            type="submit"
            aria-label="Send question"
            disabled={isStreaming}
            className="rounded-[3px] border border-hairline bg-canvas px-4 text-lg text-muted transition-colors hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            →
          </button>
        </form>
        <p className="mt-2 text-[10px] text-muted">
          Chats are saved in this browser.{' '}
          <button
            type="button"
            onClick={onNewChat}
            disabled={isStreaming || messages.length === 0}
            className="rounded-[3px] underline underline-offset-2 transition-colors hover:text-primary disabled:cursor-not-allowed disabled:no-underline"
          >
            New chat
          </button>{' '}
          clears it.
        </p>
      </div>
    </section>
  )
}

export default Chat
