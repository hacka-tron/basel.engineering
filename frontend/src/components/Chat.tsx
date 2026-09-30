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
      <div className="flex min-h-0 flex-1 flex-col">
        <div ref={messagesRef} aria-label="Messages" aria-live="off" className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-4 md:px-7 md:py-6">
          <div className="flex shrink-0 flex-col gap-4">
            {messages.map((message) => {
              const pending = message.state === 'pending'
              if (message.role === 'assistant' && !message.content && !pending) return null
              const showSources = message.role === 'assistant' && !pending && (message.sources?.length ?? 0) > 0
              return (
                <div key={message.id} className={`flex min-w-0 max-w-[90%] flex-col gap-1.5 ${message.role === 'user' ? 'self-end' : 'self-start'}`}>
                  <div
                    className={`whitespace-pre-wrap break-words rounded-[3px] border border-hairline px-4 py-3 text-sm leading-relaxed text-primary md:text-[15px] ${message.role === 'user' ? 'bg-canvas' : 'bg-panel'}`}
                  >
                    {message.content || '…'}
                  </div>
                  {showSources && (
                    <p className="break-words px-1 text-[11px] leading-relaxed text-muted">
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
                    <p className="break-words px-1 text-[11px] leading-relaxed text-muted">Searched for: {message.rewrittenQuery}</p>
                  )}
                </div>
              )
            })}
          </div>
          {/* Suggested questions return whenever this tab's conversation is empty. */}
          {messages.length === 0 && <div className="mt-auto shrink-0 pt-6">
            <p className="mb-3 text-xs text-muted">Suggested questions</p>
            {/* Chips wrap so every question is fully visible; 44px min tap height on mobile. */}
            <div className="flex flex-wrap gap-2">
              {questions[corpus].map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => setQuestion(suggestion)}
                  className="min-h-11 max-w-full whitespace-normal rounded-[3px] border border-hairline px-3 py-2 text-left text-sm leading-relaxed text-muted transition-colors hover:text-primary md:min-h-0"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>}
        </div>

      </div>

      {inputAccessory}

      <div className="shrink-0 px-4 pb-3 md:px-7 md:pb-7">
        {errorMessage && <p role="alert" className="mb-2 text-xs text-muted">{errorMessage}</p>}
        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            aria-label="Ask anything"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            disabled={isStreaming}
            placeholder="Ask anything..."
            className="min-w-0 flex-1 rounded-[3px] border border-hairline bg-canvas px-3 py-3 text-base text-primary outline-none placeholder:text-muted focus:border-cyan disabled:cursor-not-allowed"
          />
          <button
            type="submit"
            aria-label="Send question"
            disabled={isStreaming}
            className="min-w-11 rounded-[3px] border border-hairline bg-canvas px-4 text-lg text-muted transition-colors hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            →
          </button>
        </form>
        <p className="mt-2 flex flex-wrap items-center gap-x-1 text-[11px] text-muted md:mt-2">
          Chats are saved in this browser.{' '}
          <button
            type="button"
            onClick={onNewChat}
            disabled={isStreaming || messages.length === 0}
            className="-my-2.5 inline-flex min-h-11 items-center rounded-[3px] px-1 text-[11px] underline underline-offset-2 transition-colors hover:text-primary disabled:cursor-not-allowed disabled:no-underline md:my-0 md:min-h-0 md:px-0"
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
