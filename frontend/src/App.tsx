import { useCallback, useEffect, useRef, useState } from 'react'
import Chat from './components/Chat'
import ArchitecturePanel, { type WorkerPod } from './components/ArchitecturePanel'
import ContactReveal from './components/ContactReveal'
import PipelineStrip from './components/PipelineStrip'
import StatsBar from './components/StatsBar'
import { useMediaQuery } from './hooks/useMediaQuery'
import { useStressTest } from './hooks/useStressTest'
import { questionForComponent, type NodeId } from './architecture'
import { askQuestion, type RetrievalChunk } from './lib/sse'
import { connectClusterStream } from './lib/clusterStream'
import {
  historyForRequest,
  loadConversation,
  MAX_DISPLAY_MESSAGES,
  newMessageId,
  serializeConversation,
  storageKey,
  writeConversation,
  type ApiCorpus,
  type ChatMessage,
  type MessageSource,
} from './lib/conversation'

export type Corpus = 'basel' | 'system'

const CORPORA: Corpus[] = ['basel', 'system']

function apiCorpus(corpus: Corpus): ApiCorpus {
  return corpus === 'basel' ? 'about_me' : 'about_system'
}

function messageSources(chunks: RetrievalChunk[]): MessageSource[] {
  const seen = new Set<string>()
  const sources: MessageSource[] = []
  for (const chunk of chunks) {
    if (seen.has(chunk.source_path)) continue
    seen.add(chunk.source_path)
    sources.push({ source_path: chunk.source_path, title: chunk.title, ...(chunk.url ? { url: chunk.url } : {}) })
  }
  return sources
}

function App() {
  const [corpus, setCorpus] = useState<Corpus>('basel')
  const [architectureOpen, setArchitectureOpen] = useState(false)
  const [activeNode, setActiveNode] = useState<NodeId | null>(null)
  const [selectedNode, setSelectedNode] = useState<NodeId | null>(null)
  const [nodeCacheStatus, setNodeCacheStatus] = useState<Partial<Record<NodeId, 'hit' | 'miss'>>>({})
  const [retrievedChunks, setRetrievedChunks] = useState<RetrievalChunk[]>([])
  // One conversation per corpus tab (DESIGN-002 §5.1): switching the toggle
  // swaps which conversation is shown instead of mixing the two. Restored
  // from localStorage on load; the architecture panel still starts idle.
  const [conversations, setConversations] = useState<Record<Corpus, ChatMessage[]>>(() => ({
    basel: loadConversation('about_me'),
    system: loadConversation('about_system'),
  }))
  const messages = conversations[corpus]
  const conversationsRef = useRef(conversations)
  const [isStreaming, setIsStreaming] = useState(false)
  const [lastStats, setLastStats] = useState<{ latencyMs: number; cacheStatus: 'hit' | 'miss'; tokensOut?: number } | null>(null)
  const [queriesServed, setQueriesServed] = useState(0)
  // Errors belong to the conversation whose request failed.
  const [chatError, setChatError] = useState<{ corpus: Corpus; message: string } | null>(null)
  const errorMessage = chatError?.corpus === corpus ? chatError.message : null
  // Which conversation and assistant message the in-flight request writes to;
  // it stays fixed even if the visitor switches tabs mid-answer.
  const streamTargetRef = useRef<{ corpus: Corpus; messageId: string } | null>(null)
  // Last persisted form per corpus, so writes happen only when the settled
  // conversation changes (after a user message or a settled answer), never
  // per streamed token.
  const savedSignatureRef = useRef<Partial<Record<Corpus, string | null>>>({})
  const requestInFlightRef = useRef(false)
  const pendingComponentRef = useRef<NodeId | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)
  // `done.total_ms` (server-measured) spans the entire request, including
  // however long the LLM took to generate and stream the whole answer -
  // display latency should instead reflect just how fast the server started
  // responding, not how long the answer was. Measured client-side as
  // time-to-first-token, which also naturally excludes the reveal
  // animation's pacing (captured the instant a token event arrives over the
  // network, not when it's drawn on screen).
  const requestStartRef = useRef<number | null>(null)
  const firstTokenLatencyRef = useRef<number | null>(null)

  // Stress test: pod dots + backlog counter (DESIGN.md §9.4), fed by a
  // standing /api/cluster/stream connection — kept simple and always-on
  // rather than opened/closed around each stress-test click, since the
  // cluster can also scale from real (non-synthetic) traffic. `podsById`
  // undefined means "no cluster view available" (e.g. local dev without a
  // live Kubernetes API); ArchitecturePanel only renders pod dots when it's
  // defined, so this degrades to today's plain worker node rather than
  // showing a stuck/fake pod count.
  const [podsById, setPodsById] = useState<Record<string, WorkerPod> | undefined>(undefined)
  const [backlog, setBacklog] = useState<number | null>(null)
  const [shaking, setShaking] = useState(false)
  const shakeTimeoutRef = useRef<number | null>(null)
  const [simulatedPodCount, setSimulatedPodCount] = useState<number | null>(null)
  const [simulatedBacklog, setSimulatedBacklog] = useState<number | null>(null)
  const simulationTimerRef = useRef<number | null>(null)

  const startVisualStressTest = useCallback(() => {
    if (simulationTimerRef.current !== null) window.clearInterval(simulationTimerRef.current)
    const podCounts = [1, 2, 3, 3, 3, 3, 3, 2, 2, 1]
    let frame = 0
    setSimulatedPodCount(podCounts[0])
    setSimulatedBacklog(300)
    simulationTimerRef.current = window.setInterval(() => {
      frame += 1
      if (frame >= podCounts.length) {
        if (simulationTimerRef.current !== null) window.clearInterval(simulationTimerRef.current)
        simulationTimerRef.current = null
        setSimulatedPodCount(null)
        setSimulatedBacklog(null)
        return
      }
      setSimulatedPodCount(podCounts[frame])
      setSimulatedBacklog(Math.max(0, 300 - frame * 35))
    }, 850)
  }, [])

  useEffect(() => {
    const disconnect = connectClusterStream({
      onPod: (event) => {
        setPodsById((current) => {
          const next = { ...(current ?? {}) }
          if (event.type === 'DELETED') {
            delete next[event.pod]
          } else {
            next[event.pod] = { name: event.pod, ready: event.ready }
          }
          return next
        })
      },
      onBacklog: (event) => setBacklog(event.backlog),
      onUnavailable: () => {
        setPodsById(undefined)
        setBacklog(null)
      },
    })
    return disconnect
  }, [])

  useEffect(() => () => {
    if (shakeTimeoutRef.current !== null) window.clearTimeout(shakeTimeoutRef.current)
    if (simulationTimerRef.current !== null) window.clearInterval(simulationTimerRef.current)
  }, [])

  // The "earthquake" wow-moment: a brief, tasteful-but-noticeable screen
  // shake on every press, including a locked/cooldown one, since pressing
  // the button is the fun feedback moment regardless of whether this
  // particular click is the one that starts a new burst.
  const triggerShake = useCallback(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    if (shakeTimeoutRef.current !== null) window.clearTimeout(shakeTimeoutRef.current)
    setShaking(false)
    // Force a reflow-driven restart so back-to-back clicks each re-trigger
    // the CSS animation instead of the class no-op'ing because it's already
    // applied.
    requestAnimationFrame(() => {
      setShaking(true)
      shakeTimeoutRef.current = window.setTimeout(() => setShaking(false), 500)
    })
  }, [])

  // A real burst's pods come from the cluster stream. Where there's no
  // cluster view (local dev, or the stream is down), play the same worker
  // animation as the simulated run so a real click never looks like nothing
  // happened.
  const clusterViewRef = useRef(false)
  clusterViewRef.current = podsById !== undefined
  const startRealStressTest = useCallback(() => {
    if (!clusterViewRef.current) startVisualStressTest()
  }, [startVisualStressTest])

  const stressTest = useStressTest(startVisualStressTest, startRealStressTest)
  const shownWorkerPods = simulatedPodCount === null
    ? podsById && Object.values(podsById)
    : Array.from({ length: simulatedPodCount }, (_, index) => ({ name: `demo-worker-${index}`, ready: true }))
  const shownBacklog = simulatedPodCount === null ? backlog : simulatedBacklog

  function handleStressTestClick() {
    // Shake on every press, immediately — the click itself is the wow
    // moment, whether or not this particular click is the one that starts a
    // new burst (a cooldown click still gets the same satisfying jolt).
    triggerShake()
    void stressTest.trigger()
  }

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
  useEffect(() => {
    conversationsRef.current = conversations
    for (const key of CORPORA) {
      const signature = serializeConversation(conversations[key], 0)
      if (!(key in savedSignatureRef.current)) {
        // First run: this is what was just loaded; rewriting it would only
        // push its expiry forward without any new activity.
        savedSignatureRef.current[key] = signature
        continue
      }
      if (savedSignatureRef.current[key] === signature) continue
      savedSignatureRef.current[key] = signature
      writeConversation(apiCorpus(key), signature === null ? null : serializeConversation(conversations[key]))
    }
  }, [conversations])

  // Multiple browser tabs: last write wins, and a change saved in another
  // tab refreshes that conversation here unless this tab is streaming into it.
  useEffect(() => {
    function handleStorage(event: StorageEvent) {
      const changed = CORPORA.find((key) => event.key === storageKey(apiCorpus(key)))
      if (!changed || streamTargetRef.current?.corpus === changed) return
      const loaded = loadConversation(apiCorpus(changed))
      savedSignatureRef.current[changed] = serializeConversation(loaded, 0)
      setConversations((current) => ({ ...current, [changed]: loaded }))
    }
    window.addEventListener('storage', handleStorage)
    return () => window.removeEventListener('storage', handleStorage)
  }, [])

  function updateStreamingMessage(update: (message: ChatMessage) => ChatMessage | null) {
    const target = streamTargetRef.current
    if (!target) return
    setConversations((current) => ({
      ...current,
      [target.corpus]: current[target.corpus].flatMap((message) => {
        if (message.id !== target.messageId) return [message]
        const next = update(message)
        return next ? [next] : []
      }),
    }))
  }

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
        updateStreamingMessage((message) => ({ ...message, content: message.content + pending.slice(0, take) }))
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

  function finishRequest() {
    setIsStreaming(false)
    setActiveNode(null)
    requestInFlightRef.current = false
    abortControllerRef.current = null
    streamTargetRef.current = null
    const pendingComponent = pendingComponentRef.current
    pendingComponentRef.current = null
    if (pendingComponent) handleAsk(questionForComponent(pendingComponent), 'system', { sendHistory: false })
  }

  function handleAsk(question: string, targetCorpus: Corpus = corpus, { sendHistory = true } = {}) {
    if (requestInFlightRef.current) return
    requestInFlightRef.current = true
    const controller = new AbortController()
    abortControllerRef.current = controller
    // Recent settled turns of this tab's conversation, read before the new
    // question is appended. Component questions are self-contained, so they
    // skip history (and so keep their answer-cache eligibility), but they are
    // still recorded in About This System's conversation for later follow-ups.
    const history = sendHistory ? historyForRequest(conversationsRef.current[targetCorpus]) : []
    const now = Date.now()
    const assistantId = newMessageId()
    streamTargetRef.current = { corpus: targetCorpus, messageId: assistantId }
    setNodeCacheStatus({})
    setRetrievedChunks([])
    setChatError(null)
    setActiveNode(null)
    setConversations((current) => ({
      ...current,
      [targetCorpus]: [
        ...current[targetCorpus],
        { id: newMessageId(), role: 'user', content: question, createdAt: now },
        { id: assistantId, role: 'assistant', content: '', state: 'pending', createdAt: now },
      ].slice(-MAX_DISPLAY_MESSAGES) as ChatMessage[],
    }))
    setIsStreaming(true)
    revealBufferRef.current = ''
    revealFinalizeRef.current = null
    requestStartRef.current = performance.now()
    firstTokenLatencyRef.current = null

    void askQuestion(question, apiCorpus(targetCorpus), {
      onStage: (event) => {
        setActiveNode((current) => event.status === 'start'
          ? event.node
          : current === event.node ? null : current)
        if (event.cache) {
          setNodeCacheStatus((current) => ({ ...current, [event.node]: event.cache }))
        }
      },
      onRetrieval: (event) => {
        setRetrievedChunks(event.chunks)
        updateStreamingMessage((message) => ({
          ...message,
          sources: messageSources(event.chunks),
          ...(event.rewritten_query ? { rewrittenQuery: event.rewritten_query } : {}),
        }))
      },
      onToken: (event) => {
        if (firstTokenLatencyRef.current === null && requestStartRef.current !== null) {
          firstTokenLatencyRef.current = Math.round(performance.now() - requestStartRef.current)
        }
        revealBufferRef.current += event.text
        ensureRevealLoop()
      },
      onDone: (event) => {
        revealFinalizeRef.current = () => {
          updateStreamingMessage((message) => ({
            ...message,
            content: event.mode === 'retrieval_only' && !message.content
              ? 'Sources retrieved — no generated answer for this request.'
              : message.content,
            state: event.mode === 'retrieval_only' ? 'retrieval_only' : 'done',
          }))
          setLastStats({
            latencyMs: firstTokenLatencyRef.current ?? event.total_ms,
            cacheStatus: event.answer_cache,
            tokensOut: event.tokens_out,
          })
          setQueriesServed((current) => current + 1)
          finishRequest()
        }
        ensureRevealLoop()
      },
      onError: (event) => {
        revealFinalizeRef.current = () => {
          setChatError({
            corpus: targetCorpus,
            message: event.code === 'rate_limited' && event.retry_after_s
              ? `${event.message} Try again in ${event.retry_after_s} seconds.`
              : event.message || 'Something went wrong — try again.',
          })
          // An empty reply is dropped; partial text stays visible but is
          // marked `error`, so it is never saved or sent back as history.
          updateStreamingMessage((message) => message.content ? { ...message, state: 'error' } : null)
          finishRequest()
        }
        ensureRevealLoop()
      },
    }, controller.signal, history)
  }

  function handleNewChat() {
    if (requestInFlightRef.current) return
    setConversations((current) => ({ ...current, [corpus]: [] }))
    if (chatError?.corpus === corpus) setChatError(null)
    setRetrievedChunks([])
    setNodeCacheStatus({})
    if (corpus === 'system') setSelectedNode(null)
  }

  function handleInspectComponent(id: NodeId) {
    setSelectedNode(id)
    setCorpus('system')
    if (isDesktop) setArchitectureOpen(false)
    if (requestInFlightRef.current) {
      pendingComponentRef.current = id
    } else {
      handleAsk(questionForComponent(id), 'system', { sendHistory: false })
    }
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
  const selectedQuestion = selectedNode ? questionForComponent(selectedNode) : null
  // Component questions always go to About This System's conversation.
  const systemMessages = conversations.system
  const systemError = chatError?.corpus === 'system' ? chatError.message : null
  const selectedAnswer = selectedNode && systemError ? systemError
    : selectedQuestion && systemMessages.at(-2)?.content === selectedQuestion && systemMessages.at(-1)?.role === 'assistant'
      ? systemMessages.at(-1)?.content || ''
      : selectedNode ? 'Waiting for the current answer…' : null

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

  // Rendered before the Contact group at md+ and after it below md so DOM/tab
  // order matches the visual order at both layouts (visual order via `order-*`).
  const topicNav = (
      <nav aria-label="Question topic" className="order-3 flex w-full items-center gap-2 text-xs md:order-2 md:ml-4 md:w-auto">
        <button
          type="button"
          aria-pressed={corpus === 'basel'}
          onClick={() => { setCorpus('basel'); setSelectedNode(null); pendingComponentRef.current = null }}
          className={`inline-flex min-h-11 items-center rounded-[3px] px-3 transition-colors hover:text-primary md:min-h-0 md:py-2 ${corpus === 'basel' ? 'text-cyan' : 'text-muted'}`}
        >
          About Basel
        </button>
        <span aria-hidden="true" className="text-hairline">|</span>
        <button
          type="button"
          aria-pressed={corpus === 'system'}
          onClick={() => setCorpus('system')}
          className={`inline-flex min-h-11 items-center rounded-[3px] px-3 transition-colors hover:text-primary md:min-h-0 md:py-2 ${corpus === 'system' ? 'text-cyan' : 'text-muted'}`}
        >
          About This System
        </button>
      </nav>
  )

  return (
    <div className={`flex h-dvh min-h-0 flex-col overflow-hidden bg-canvas font-mono text-primary ${shaking ? 'earthquake-shake' : ''}`}>
      <header className="relative flex min-h-[72px] shrink-0 flex-wrap items-center gap-x-4 gap-y-0 border-b border-hairline px-4 py-0 md:flex-nowrap md:gap-0 md:px-8 md:py-0">
        {/*
          Below md: row one is [h1 ... Contact + GitHub], row two is the topic
          nav (flex-wrap; the revealed email wraps onto its own row). Visual
          order is set with `order-*`; at md+ everything sits on one row as
          [h1, nav ... Contact + GitHub].
        */}
        <h1 className="order-1 flex min-h-11 shrink-0 items-center whitespace-nowrap md:min-h-0 text-[clamp(1rem,0.9rem+0.5vw,1.25rem)] font-semibold tracking-tight">Basel Abdel-Rahman</h1>
          {isDesktop && topicNav}

        <div className="order-2 flex flex-wrap items-center gap-x-4 gap-y-0 md:order-3 md:ml-auto md:gap-3 md:flex-nowrap">
          <ContactReveal />
          <a
            href="https://github.com/hacka-tron/basel.engineering"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="View source on GitHub"
            className="absolute right-0.5 top-0 flex size-11 items-center justify-center text-muted transition-colors hover:text-primary md:static md:size-auto"
          >
            <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
            </svg>
          </a>
        </div>
        {!isDesktop && topicNav}
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[40%_60%]">
        <Chat
          corpus={corpus}
          messages={messages}
          isStreaming={isStreaming}
          onAsk={(question) => { setSelectedNode(null); handleAsk(question) }}
          onNewChat={handleNewChat}
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
        {isDesktop && <ArchitecturePanel fitMinZoom={0.65} activeNode={activeNode} nodeCacheStatus={nodeCacheStatus} retrievedChunks={retrievedChunks} selectedNode={selectedNode} onInspect={handleInspectComponent} workerPods={shownWorkerPods} backlog={shownBacklog} />}
      </main>

      <StatsBar
        lastStats={lastStats}
        queriesServed={queriesServed}
        onStressTest={handleStressTestClick}
        stressTestCooldownSeconds={stressTest.cooldownSeconds}
        stressTestSubmitting={stressTest.isSubmitting}
        stressTestCapacity={stressTest.capacity}
        stressTestRealCooldownSeconds={stressTest.realCooldownSeconds}
      />

      {showArchitectureSheet && (
        <div role="dialog" aria-modal="true" aria-label="Architecture" className="fixed inset-0 z-50 md:hidden">
          <button
            type="button"
            aria-label="Close architecture panel"
            onClick={closeArchitectureSheet}
            className="absolute inset-0 bg-black/70"
          />
          <div className="absolute inset-x-0 bottom-0 flex h-[80dvh] flex-col border-t border-hairline bg-panel pb-[env(safe-area-inset-bottom)]">
            <div className="flex h-12 shrink-0 items-center justify-between border-b border-hairline px-4">
              <h2 className="text-sm text-primary">Architecture</h2>
              <button
                ref={closeButtonRef}
                type="button"
                aria-label="Close architecture panel"
                onClick={closeArchitectureSheet}
                className="min-h-11 min-w-11 text-xl text-muted hover:text-primary"
              >
                ×
              </button>
            </div>
            <div className="flex min-h-0 flex-1 flex-col [&>section]:flex-1">
              <ArchitecturePanel fitMinZoom={0.75} activeNode={activeNode} nodeCacheStatus={nodeCacheStatus} retrievedChunks={retrievedChunks} selectedNode={selectedNode} answerText={selectedAnswer} onInspect={handleInspectComponent} workerPods={shownWorkerPods} backlog={shownBacklog} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
