import { useEffect, useRef, useState } from 'react'
import Chat from './components/Chat'
import ArchitecturePanel from './components/ArchitecturePanel'
import ContactReveal from './components/ContactReveal'
import PipelineStrip from './components/PipelineStrip'
import StatsBar from './components/StatsBar'
import { useMediaQuery } from './hooks/useMediaQuery'
import type { NodeId } from './architecture'
import { askQuestion, type RetrievalChunk } from './lib/sse'

export type Corpus = 'basel' | 'system'

function App() {
  const [corpus, setCorpus] = useState<Corpus>('basel')
  const [architectureOpen, setArchitectureOpen] = useState(false)
  const [activeNode, setActiveNode] = useState<NodeId | null>(null)
  const [nodeCacheStatus, setNodeCacheStatus] = useState<Partial<Record<NodeId, 'hit' | 'miss'>>>({})
  const [retrievedChunks, setRetrievedChunks] = useState<RetrievalChunk[]>([])
  const [messages, setMessages] = useState<{ role: 'user' | 'assistant'; text: string }[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [lastStats, setLastStats] = useState<{ totalMs: number; cacheStatus: 'hit' | 'miss'; tokensOut?: number } | null>(null)
  const [queriesServed, setQueriesServed] = useState(0)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const requestInFlightRef = useRef(false)
  const abortControllerRef = useRef<AbortController | null>(null)

  // Real per-token SSE events are the source of truth for *content*, but
  // nothing paces how fast they're *shown*. A short/cached answer (or the
  // local `fake` provider, which has no artificial latency at all) can have
  // its entire text arrive within a single network read, which React can
  // even coalesce into one paint — the answer then appears all at once
  // instead of reading out like a typewriter. This buffer decouples arrival
  // from reveal: tokens land here immediately, and a fixed-cadence timer
  // drains them into the visible message a little at a time. It never
  // fabricates content — it only paces real text that has already arrived —
  // and it self-adjusts (revealing a larger fraction of a bigger backlog per
  // tick) so a genuinely slow, naturally-paced real stream is shown at
  // essentially the same speed it arrives, while a burst reads out visibly.
  const revealBufferRef = useRef('')
  const revealTimerRef = useRef<number | null>(null)
  const revealFinalizeRef = useRef<(() => void) | null>(null)
  const REVEAL_TICK_MS = 30

  function ensureRevealLoop() {
    if (revealTimerRef.current !== null) return
    revealTimerRef.current = window.setInterval(() => {
      const pending = revealBufferRef.current
      if (pending.length > 0) {
        const take = Math.max(2, Math.ceil(pending.length / 12))
        revealBufferRef.current = pending.slice(take)
        setMessages((current) => current.map((message, index) =>
          index === current.length - 1 && message.role === 'assistant'
            ? { ...message, text: message.text + pending.slice(0, take) }
            : message))
        return
      }
      if (revealFinalizeRef.current) {
        const finalize = revealFinalizeRef.current
        revealFinalizeRef.current = null
        if (revealTimerRef.current !== null) {
          window.clearInterval(revealTimerRef.current)
          revealTimerRef.current = null
        }
        finalize()
      }
    }, REVEAL_TICK_MS)
  }

  useEffect(() => () => {
    abortControllerRef.current?.abort()
    if (revealTimerRef.current !== null) window.clearInterval(revealTimerRef.current)
  }, [])

  function handleAsk(question: string) {
    if (requestInFlightRef.current || isStreaming) return
    requestInFlightRef.current = true
    const controller = new AbortController()
    abortControllerRef.current = controller
    setNodeCacheStatus({})
    setRetrievedChunks([])
    setErrorMessage(null)
    setActiveNode(null)
    setMessages((current) => [...current, { role: 'user', text: question }, { role: 'assistant', text: '' }])
    setIsStreaming(true)
    revealBufferRef.current = ''
    revealFinalizeRef.current = null

    void askQuestion(question, corpus === 'basel' ? 'about_me' : 'about_system', {
      onStage: (event) => {
        setActiveNode((current) => event.status === 'start'
          ? event.node
          : current === event.node ? null : current)
        if (event.cache) {
          setNodeCacheStatus((current) => ({ ...current, [event.node]: event.cache }))
        }
      },
      onRetrieval: (event) => setRetrievedChunks(event.chunks),
      onToken: (event) => {
        revealBufferRef.current += event.text
        ensureRevealLoop()
      },
      onDone: (event) => {
        revealFinalizeRef.current = () => {
          if (event.mode === 'retrieval_only') {
            setMessages((current) => current.map((message, index) =>
              index === current.length - 1 && message.role === 'assistant' && !message.text
                ? { ...message, text: 'Sources retrieved — no generated answer for this request.' }
                : message))
          }
          setLastStats({ totalMs: event.total_ms, cacheStatus: event.answer_cache, tokensOut: event.tokens_out })
          setQueriesServed((current) => current + 1)
          setIsStreaming(false)
          setActiveNode(null)
          requestInFlightRef.current = false
          abortControllerRef.current = null
        }
        ensureRevealLoop()
      },
      onError: (event) => {
        revealFinalizeRef.current = () => {
          setErrorMessage(event.code === 'rate_limited' && event.retry_after_s
            ? `${event.message} Try again in ${event.retry_after_s} seconds.`
            : event.message || 'Something went wrong — try again.')
          setMessages((current) => current.at(-1)?.role === 'assistant' && !current.at(-1)?.text
            ? current.slice(0, -1)
            : current)
          setIsStreaming(false)
          setActiveNode(null)
          requestInFlightRef.current = false
          abortControllerRef.current = null
        }
        ensureRevealLoop()
      },
    }, controller.signal)
  }
  // Matches Tailwind's `md` breakpoint. Drives which ArchitecturePanel /
  // React Flow instance is mounted so only one ever exists at a time — see
  // the comment above the desktop panel render below.
  const isDesktop = useMediaQuery('(min-width: 768px)')
  // Derived rather than synced via an effect: even if `architectureOpen`
  // stays true while the viewport crosses into desktop width (e.g. rotating
  // a tablet), the sheet — and its ArchitecturePanel/React Flow instance —
  // simply never renders at md+, so it can never coexist with the desktop
  // panel below.
  const showArchitectureSheet = architectureOpen && !isDesktop

  // Focus management for the mobile bottom sheet: it declares
  // `aria-modal="true"`, which is a promise to assistive tech that focus is
  // contained. A full Tab-cycling focus trap is out of scope for this
  // skeleton, but Escape-to-close, initial focus into the dialog, and focus
  // restoration on close are the minimum needed to honor that promise.
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const triggerButtonRef = useRef<HTMLButtonElement | null>(null)

  const closeArchitectureSheet = () => {
    setArchitectureOpen(false)
    triggerButtonRef.current?.focus()
  }

  useEffect(() => {
    if (!showArchitectureSheet) return

    closeButtonRef.current?.focus()

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setArchitectureOpen(false)
        triggerButtonRef.current?.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [showArchitectureSheet])

  return (
    <div className="flex h-screen min-h-0 flex-col overflow-hidden bg-canvas font-mono text-primary">
      <header className="flex min-h-[72px] shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-hairline px-4 py-3 md:h-[72px] md:flex-nowrap md:gap-0 md:px-8 md:py-0">
        {/*
          Below md, the header wraps to two rows: [h1 + nav] on row one,
          [Contact + GitHub] on row two. `md:contents` removes the h1/nav
          wrapper from layout at md+, so those become direct header
          children again there; the Contact/GitHub group's own `w-full`
          (mobile) vs `md:w-auto md:ml-auto` (desktop) is what forces it
          onto its own row below md and pushes it to the right at md+ -
          the corpus toggle lives with the logo group (site identity +
          navigation on the left), while Contact/GitHub are the two "meta"
          actions grouped together on the right. `flex-wrap` on the logo
          group is a safety net, not the primary layout: h1 stays on one
          line (shrink-0/whitespace-nowrap) and nav drops to its own line
          only if there's genuinely not enough room (e.g. ~375px), rather
          than squeezing h1's text.
        */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 md:contents">
          <h1 className="shrink-0 whitespace-nowrap text-base font-semibold tracking-tight">Basel Abdel-Rahman</h1>
          <nav aria-label="Question topic" className="flex items-center gap-2 text-xs md:ml-4">
            <button
              type="button"
              aria-pressed={corpus === 'basel'}
              onClick={() => setCorpus('basel')}
              className={`rounded-[3px] px-3 py-2 transition-colors hover:text-primary ${corpus === 'basel' ? 'text-cyan' : 'text-muted'}`}
            >
              About Basel
            </button>
            <span aria-hidden="true" className="text-hairline">|</span>
            <button
              type="button"
              aria-pressed={corpus === 'system'}
              onClick={() => setCorpus('system')}
              className={`rounded-[3px] px-3 py-2 transition-colors hover:text-primary ${corpus === 'system' ? 'text-cyan' : 'text-muted'}`}
            >
              About This System
            </button>
          </nav>
        </div>

        <div className="flex w-full flex-wrap items-center gap-3 md:w-auto md:flex-nowrap md:ml-auto">
          <ContactReveal />
          <a
            href="https://github.com/hacka-tron/basel.engineering"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="View source on GitHub"
            className="text-muted transition-colors hover:text-primary"
          >
            <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
            </svg>
          </a>
        </div>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[40%_60%]">
        <Chat
          corpus={corpus}
          messages={messages}
          isStreaming={isStreaming}
          onAsk={handleAsk}
          errorMessage={errorMessage}
          inputAccessory={
            <PipelineStrip
              activeNode={activeNode}
              triggerRef={triggerButtonRef}
              onViewArchitecture={() => setArchitectureOpen(true)}
            />
          }
        />
        {/*
          Mounted only at md+ instead of CSS-hidden below it: React Flow sets
          up a ResizeObserver, zoom/pan handlers, and internal store on mount,
          so leaving it always-mounted-but-hidden on mobile wastes work and
          risks a second instance rendering into a zero-size container
          whenever the sheet below is also open. Only one ArchitecturePanel
          is ever mounted at a time (this one, or the sheet's).
        */}
        {isDesktop && <ArchitecturePanel activeNode={activeNode} nodeCacheStatus={nodeCacheStatus} retrievedChunks={retrievedChunks} />}
      </main>

      <StatsBar lastStats={lastStats} queriesServed={queriesServed} />

      {showArchitectureSheet && (
        <div role="dialog" aria-modal="true" aria-label="Architecture" className="fixed inset-0 z-50 md:hidden">
          <button
            type="button"
            aria-label="Close architecture panel"
            onClick={closeArchitectureSheet}
            className="absolute inset-0 bg-black/70"
          />
          <div className="absolute inset-x-0 bottom-0 flex h-[80dvh] flex-col border-t border-hairline bg-panel">
            <div className="flex h-12 shrink-0 items-center justify-between border-b border-hairline px-4">
              <h2 className="text-sm text-primary">Architecture</h2>
              <button
                ref={closeButtonRef}
                type="button"
                aria-label="Close architecture panel"
                onClick={closeArchitectureSheet}
                className="px-2 text-xl text-muted hover:text-primary"
              >
                ×
              </button>
            </div>
            <div className="flex min-h-0 flex-1 flex-col [&>section]:flex-1">
              <ArchitecturePanel activeNode={activeNode} nodeCacheStatus={nodeCacheStatus} retrievedChunks={retrievedChunks} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
