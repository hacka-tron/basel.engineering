import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import type { Corpus } from '../App'

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
  messages: { role: 'user' | 'assistant'; text: string }[]
  isStreaming: boolean
  onAsk: (question: string) => void
  errorMessage: string | null
  inputAccessory?: ReactNode
}

function Chat({ corpus, messages, isStreaming, onAsk, errorMessage, inputAccessory }: ChatProps) {
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
  const announcement = !isStreaming && lastMessage?.role === 'assistant' ? lastMessage.text : ''

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
            {messages.map((message, index) => (
              (message.text || message.role === 'user' || (isStreaming && index === messages.length - 1)) && (
                <div
                  key={index}
                  className={`max-w-[90%] whitespace-pre-wrap break-words rounded-[3px] border border-hairline px-4 py-3 text-sm leading-relaxed text-primary ${message.role === 'user' ? 'self-end bg-canvas' : 'self-start bg-panel'}`}
                >
                  {message.text || (isStreaming ? '…' : '')}
                </div>
              )
            ))}
          </div>
        </div>

        <div className="shrink-0 px-7 pb-6">
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
        </div>
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
      </div>
    </section>
  )
}

export default Chat
