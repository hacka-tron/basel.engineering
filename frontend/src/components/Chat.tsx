import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type MouseEvent, type ReactNode } from 'react'
import type { Corpus } from '../App'
import type { ChatMessage } from '../lib/conversation'
import suggestedQuestions from '../suggested-questions.json'

// One list shared with the backend: `python -m services.glassbox.warm` asks
// these same questions to keep their answers cached (DESIGN.md §7.3).
const questions: Record<Corpus, string[]> = {
  basel: suggestedQuestions.about_me,
  system: suggestedQuestions.about_system,
}

type ChatProps = {
  corpus: Corpus
  messages: ChatMessage[]
  isStreaming: boolean
  onAsk: (question: string) => void
  onStop: () => void
  onNewChat: () => void
  inputAccessory?: ReactNode
  /** Below md, the topic chips; rendered directly above the ask box. */
  inputTopic?: ReactNode
  /**
   * Shown in place of the messages (mobile diagram view). The messages stay
   * mounted underneath, so their scroll position survives the switch.
   */
  replacement?: ReactNode
  onInputFocusChange?: (focused: boolean) => void
}

// Within this distance of the bottom counts as "at the bottom" (DESIGN-002 §6.3).
const FOLLOW_THRESHOLD_PX = 80
// Send turns into Stop in place; a double-click on Send must not stop the
// answer it just asked for.
const STOP_GUARD_MS = 400

function Chat({ corpus, messages, isStreaming, onAsk, onStop, onNewChat, inputAccessory, inputTopic, replacement, onInputFocusChange }: ChatProps) {
  const [question, setQuestion] = useState('')
  const messagesRef = useRef<HTMLDivElement>(null)
  const askedAtRef = useRef(0)
  // Smart auto-scroll (DESIGN-002 §6.3): follow new content only while the
  // visitor is at the bottom; scrolling up pauses it and offers a pill back.
  const [following, setFollowing] = useState(true)
  const [followedCorpus, setFollowedCorpus] = useState(corpus)
  if (followedCorpus !== corpus) {
    // Each tab's conversation opens at its latest message.
    setFollowedCorpus(corpus)
    setFollowing(true)
  }

  useLayoutEffect(() => {
    const container = messagesRef.current
    if (container && following) container.scrollTop = container.scrollHeight
  }, [messages, following])

  // The list also changes height without new messages: focus mode sliding the
  // header and footer away, the keyboard, rotating, or coming back from the
  // diagram view. Stay pinned to the latest message through all of them.
  const followingRef = useRef(following)
  useEffect(() => {
    followingRef.current = following
  }, [following])
  useEffect(() => {
    const container = messagesRef.current
    if (!container) return
    const observer = new ResizeObserver(() => {
      if (followingRef.current) container.scrollTop = container.scrollHeight
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  function handleScroll() {
    const container = messagesRef.current
    if (!container) return
    setFollowing(container.scrollHeight - container.scrollTop - container.clientHeight <= FOLLOW_THRESHOLD_PX)
  }

  function handleStop() {
    if (performance.now() - askedAtRef.current < STOP_GUARD_MS) return
    onStop()
  }

  // The message list mutates on every streamed token; a live region on the
  // whole list would re-announce (or re-read) on each mutation. Instead, a
  // separate off-screen live region holds the settled assistant text only —
  // it's empty for the whole duration of a stream (so no per-token
  // announcements) and becomes non-empty in a single mutation once
  // `isStreaming` flips false, which a screen reader announces once.
  const lastMessage = messages[messages.length - 1]
  const announcement = !isStreaming && lastMessage?.role === 'assistant' ? lastMessage.content : ''

  // A suggested question is asked straight away, the same way as Send.
  function handleSuggestionClick(event: MouseEvent<HTMLButtonElement>) {
    const suggestion = event.currentTarget.value
    if (isStreaming || !suggestion) return
    askedAtRef.current = performance.now()
    setFollowing(true)
    onAsk(suggestion)
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedQuestion = question.trim()
    if (!trimmedQuestion || isStreaming) return
    askedAtRef.current = performance.now()
    setFollowing(true)
    onAsk(trimmedQuestion)
    setQuestion('')
  }

  return (
    <section aria-label="Chat" className="flex min-h-0 min-w-0 flex-col bg-panel md:border-r md:border-hairline">
      <div aria-live="polite" className="sr-only">{announcement}</div>
      {replacement}
      <div className={`relative min-h-0 flex-1 flex-col ${replacement ? 'hidden' : 'flex'}`}>
        <div ref={messagesRef} onScroll={handleScroll} aria-label="Messages" aria-live="off" className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-4 md:px-7 md:py-6">
          <div className="flex shrink-0 flex-col gap-4">
            {messages.map((message) => {
              const pending = message.state === 'pending'
              const stopped = message.role === 'assistant' && message.state === 'stopped'
              // A reply stopped before its first token keeps only the label.
              if (message.role === 'assistant' && !message.content && !pending && !stopped) return null
              const showSources = message.role === 'assistant' && !pending && (message.sources?.length ?? 0) > 0
              return (
                <div key={message.id} className={`flex min-w-0 max-w-[90%] flex-col gap-1.5 ${message.role === 'user' ? 'self-end' : 'self-start'}`}>
                  {(message.content || pending) && <div
                    // Failure replies and text cut off by a failure get a dashed border.
                    className={`whitespace-pre-wrap break-words rounded-[3px] border px-4 py-3 text-sm leading-relaxed md:text-[15px] ${message.role === 'user' ? 'bg-canvas' : 'bg-panel'} ${message.state === 'error' ? 'border-dashed border-hairline text-muted' : 'border-hairline text-primary'}`}
                  >
                    {message.content || '…'}
                  </div>}
                  {stopped && <p className="px-1 text-[11px] leading-relaxed text-muted">Stopped</p>}
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
                  value={suggestion}
                  onClick={handleSuggestionClick}
                  disabled={isStreaming}
                  className="min-h-11 max-w-full whitespace-normal rounded-[3px] border border-hairline px-3 py-2 text-left text-sm leading-relaxed text-muted transition-colors hover:text-primary disabled:cursor-not-allowed disabled:hover:text-muted md:min-h-0"
                >
                  {suggestion}
                </button>
              ))}
            </div>
            {/* Below md the New chat control is in the footer; this keeps the
                privacy note discoverable without a row under the ask box. */}
            <p className="mt-3 text-[11px] leading-relaxed text-muted md:hidden">Chats are saved in this browser. New chat, at the bottom, clears this topic.</p>
          </div>}
        </div>

        {!following && messages.length > 0 && (
          <button
            type="button"
            onClick={() => setFollowing(true)}
            className="absolute bottom-3 left-1/2 inline-flex min-h-11 -translate-x-1/2 items-center rounded-full border border-hairline bg-canvas px-4 text-xs text-muted shadow-sm transition-colors hover:text-primary md:min-h-0 md:py-1.5"
          >
            Jump to latest ↓
          </button>
        )}
      </div>

      {inputAccessory}

      <div className={`shrink-0 px-4 md:px-7 md:pb-7 md:pt-0 ${inputTopic ? 'pb-2' : 'py-2'}`}>
        {inputTopic}
        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            onFocus={() => onInputFocusChange?.(true)}
            onBlur={() => onInputFocusChange?.(false)}
            aria-label="Ask anything"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            disabled={isStreaming}
            placeholder="Ask anything..."
            className="min-w-0 flex-1 rounded-[3px] border border-hairline bg-canvas px-3 py-3 text-base text-primary outline-none placeholder:text-muted focus:border-cyan disabled:cursor-not-allowed"
          />
          {isStreaming ? (
            // Replaces Send while an answer streams (DESIGN-002 §6.1/§6.2).
            <button
              type="button"
              onClick={handleStop}
              aria-label="Stop answer"
              className="min-h-11 min-w-11 rounded-[3px] border border-hairline bg-canvas px-4 text-sm text-muted transition-colors hover:text-primary focus-visible:text-primary"
            >
              Stop
            </button>
          ) : (
            <button
              type="submit"
              aria-label="Send question"
              className="min-w-11 rounded-[3px] border border-hairline bg-canvas px-4 text-lg text-muted transition-colors hover:text-primary"
            >
              →
            </button>
          )}
        </form>
        {/* Desktop only; below md New chat sits in the footer after the stats. */}
        <p className="mt-2 hidden flex-wrap items-center gap-x-1 text-[11px] text-muted md:mt-2 md:flex">
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
