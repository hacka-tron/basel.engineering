import { useCallback, useEffect, useRef, useState } from 'react'
import Chat from './components/Chat'
import ArchitecturePanel, { type WorkerPod } from './components/ArchitecturePanel'
import Collapsible from './components/Collapsible'
import ContactReveal from './components/ContactReveal'
import PipelineStrip from './components/PipelineStrip'
import TopicChips, { type TopicChip } from './components/TopicChips'
import { createDiagramNav, viewFromHistoryState, type MobileView } from './lib/diagramNav'
import StatsBar from './components/StatsBar'
import { useFullNameFits } from './hooks/useFullNameFits'
import { FULL_NAME, SHORT_NAME } from './lib/headerName'
import { useMediaQuery } from './hooks/useMediaQuery'
import { DESKTOP_QUERY, PHONE_LANDSCAPE_QUERY } from './lib/layout'
import { useStressTest } from './hooks/useStressTest'
import { questionForComponent, type NodeId } from './architecture'
import { askQuestion, type RetrievalChunk } from './lib/sse'
import type { LastStats } from './lib/lastStats'
import { errorReplyFor } from './lib/errorReplies'
import { isCanonicalIdk, pickIdkReply } from './lib/idkReplies'
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

const NAME_TEXT = 'text-[clamp(1rem,0.9rem+0.5vw,1.25rem)] font-semibold tracking-tight'

export type Corpus = 'basel' | 'system'

const CORPORA: Corpus[] = ['basel', 'system']

const TOPIC_CHIPS: TopicChip<Corpus>[] = [
  { value: 'basel', label: 'About Basel', short: 'Basel' },
  { value: 'system', label: 'About This System', short: 'System' },
]

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
  // Below md the diagram replaces the conversation in place (no overlay).
  // A reload while in the diagram keeps it (its history entry survives).
  const [mobileView, setMobileView] = useState<MobileView>(() => viewFromHistoryState(window.history.state))
  // Focus mode: below md, the header and footer slide away while the ask box
  // has focus, so the conversation keeps its room with the keyboard up.
  const [askFocused, setAskFocused] = useState(false)
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
  const [lastStats, setLastStats] = useState<LastStats | null>(null)
  const [queriesServed, setQueriesServed] = useState(0)
  // The last friendly failure reply shown, so the next one is never the same.
  const lastErrorReplyRef = useRef<string | null>(null)
  const lastIdkReplyRef = useRef<string | null>(null)
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

  // Every stress-test tap shows the diagram on mobile at once: one that starts
  // a run, and one ignored while a run is in flight or counting down (the
  // animation is still playing there). It runs before any request, so a
  // later Chat tap while the request is pending wins. A long press only shows
  // details and never taps. Refs: the nav is created further down.
  const isDesktopRef = useRef(false)
  const revealDiagramRef = useRef<(isDesktop: boolean) => void>(() => {})
  const onStressTap = useCallback(() => {
    revealDiagramRef.current(isDesktopRef.current)
  }, [])
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

  function stopRevealLoop() {
    if (revealTimerRef.current !== null) {
      window.clearInterval(revealTimerRef.current)
      revealTimerRef.current = null
    }
  }

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
        stopRevealLoop()
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

    // Callbacks from a request the visitor already stopped are ignored; the
    // next request owns the shared refs by then.
    const isCurrent = () => abortControllerRef.current === controller

    void askQuestion(question, apiCorpus(targetCorpus), {
      onStage: (event) => {
        if (!isCurrent()) return
        setActiveNode((current) => event.status === 'start'
          ? event.node
          : current === event.node ? null : current)
        if (event.cache) {
          setNodeCacheStatus((current) => ({ ...current, [event.node]: event.cache }))
        }
      },
      onRetrieval: (event) => {
        if (!isCurrent()) return
        setRetrievedChunks(event.chunks)
        updateStreamingMessage((message) => ({
          ...message,
          sources: messageSources(event.chunks),
          ...(event.rewritten_query ? { rewrittenQuery: event.rewritten_query } : {}),
        }))
      },
      onToken: (event) => {
        if (!isCurrent()) return
        if (firstTokenLatencyRef.current === null && requestStartRef.current !== null) {
          firstTokenLatencyRef.current = Math.round(performance.now() - requestStartRef.current)
        }
        revealBufferRef.current += event.text
        ensureRevealLoop()
      },
      onDone: (event) => {
        if (!isCurrent()) return
        revealFinalizeRef.current = () => {
          // The sources didn't cover the question: swap the plain sentence for a
          // playful one (not the last shown here, nor the latest saved above).
          let idkReply: string | null = null
          if (event.abstained && event.mode === 'full') {
            const savedIdk = conversationsRef.current[targetCorpus]
              .findLast((message) => message.role === 'assistant' && message.idk)?.content
            idkReply = pickIdkReply([lastIdkReplyRef.current, savedIdk], Math.random, targetCorpus)
            lastIdkReplyRef.current = idkReply
          }
          updateStreamingMessage((message) => ({
            ...message,
            // Budget reached or LLM switched off: the sources still came back.
            // Only the bare canonical sentence is swapped (never a real answer).
            ...(idkReply && isCanonicalIdk(message.content) ? { content: idkReply, idk: true } : {
              content: event.mode === 'retrieval_only' && !message.content
                ? "I can't write a full answer right now, but the sources I found for this are below — they should point you the right way."
                : message.content,
            }),
            state: event.mode === 'retrieval_only' || event.mode === 'stopped' ? event.mode : 'done',
          }))
          // No token (sources only): keep firstTokenMs null so the footer
          // labels the whole-request time as total, not as a time to first token.
          setLastStats({
            firstTokenMs: firstTokenLatencyRef.current,
            totalMs: event.total_ms,
            cacheStatus: event.answer_cache,
          })
          setQueriesServed((current) => current + 1)
          finishRequest()
        }
        ensureRevealLoop()
      },
      onError: (event) => {
        if (!isCurrent()) return
        revealFinalizeRef.current = () => {
          // The technical detail is for developers; visitors get a chat reply.
          console.warn(`Ask request failed (${event.code}): ${event.message}`)
          // Avoid the last reply shown in this session and the latest one saved
          // in this tab (it may be right above after a reload).
          const savedReply = conversationsRef.current[targetCorpus]
            .findLast((message) => message.role === 'assistant' && message.state === 'error')?.content
          const reply = errorReplyFor(event, [lastErrorReplyRef.current, savedReply])
          lastErrorReplyRef.current = reply
          // The failure becomes an assistant message marked `error`: saved with
          // the conversation, but never sent as history or counted as an answer.
          // Partial text stays visible and is marked `error` too.
          const target = streamTargetRef.current
          if (target) {
            const errorReply: ChatMessage = { id: newMessageId(), role: 'assistant', content: reply, state: 'error', createdAt: Date.now() }
            setConversations((current) => ({
              ...current,
              [target.corpus]: current[target.corpus].flatMap((message) => {
                if (message.id !== target.messageId) return [message]
                return message.content ? [{ ...message, state: 'error' as const }, errorReply] : [errorReply]
              }).slice(-MAX_DISPLAY_MESSAGES),
            }))
          }
          finishRequest()
        }
        ensureRevealLoop()
      },
    }, controller.signal, history)
  }

  // Stop button (DESIGN-002 §6.2): aborting the fetch closes the connection,
  // which makes the server cancel generation. The partial answer is kept,
  // marked `stopped` (so it is saved and sent as history like any settled
  // answer), and the chat is immediately free for the next question.
  function handleStop() {
    const controller = abortControllerRef.current
    if (!requestInFlightRef.current || !controller) return
    const pendingText = revealBufferRef.current
    revealBufferRef.current = ''
    const finalize = revealFinalizeRef.current
    revealFinalizeRef.current = null
    stopRevealLoop()
    if (finalize) {
      // The answer already finished arriving and is only still being revealed:
      // show the rest now and settle it normally instead of calling it stopped.
      if (pendingText) updateStreamingMessage((message) => ({ ...message, content: message.content + pendingText }))
      finalize()
      return
    }
    controller.abort()
    updateStreamingMessage((message) => ({ ...message, content: message.content + pendingText, state: 'stopped' }))
    finishRequest()
  }

  function handleNewChat() {
    if (requestInFlightRef.current) return
    setConversations((current) => ({ ...current, [corpus]: [] }))
    setRetrievedChunks([])
    setNodeCacheStatus({})
    if (corpus === 'system') setSelectedNode(null)
  }

  function handleInspectComponent(id: NodeId) {
    setSelectedNode(id)
    setCorpus('system')
    if (requestInFlightRef.current) {
      pendingComponentRef.current = id
    } else {
      handleAsk(questionForComponent(id), 'system', { sendHistory: false })
    }
  }
  // Matches Tailwind's `md` breakpoint (redefined in index.css so a phone held
  // sideways keeps the phone layout). Drives which ArchitecturePanel / React
  // Flow instance is mounted so only one ever exists at a time — see the
  // comment above the desktop panel render below.
  const isDesktop = useMediaQuery(DESKTOP_QUERY)
  const isPhoneLandscape = useMediaQuery(PHONE_LANDSCAPE_QUERY)
  const showDiagramView = !isDesktop && mobileView === 'diagram'
  const focusMode = !isDesktop && askFocused
  // A phone held sideways is too short for the header and the diagram, so
  // the header slides away in Diagram view (the footer stays: stress test).
  const headerOpen = !focusMode && !(isPhoneLandscape && showDiagramView)

  // The diagram view is a history entry, so the browser's Back button (and
  // Escape, "Chat", or "Continue in chat") returns to the conversation.
  const diagramButtonRef = useRef<HTMLButtonElement | null>(null)
  const [diagramNav] = useState(() => createDiagramNav({
    history: window.history,
    setView: setMobileView,
    afterRender: (callback) => { requestAnimationFrame(callback) },
    focusDiagramToggle: () => diagramButtonRef.current?.focus(),
  }))
  const showMobileView = diagramNav.showView
  isDesktopRef.current = isDesktop
  revealDiagramRef.current = diagramNav.revealDiagram
  useEffect(() => {
    function handlePopState(event: PopStateEvent) {
      diagramNav.handlePopState(event.state)
    }
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [diagramNav])
  useEffect(() => {
    if (!showDiagramView) return
    document.addEventListener('keydown', diagramNav.handleKeyDown)
    return () => document.removeEventListener('keydown', diagramNav.handleKeyDown)
  }, [showDiagramView, diagramNav])

  // Leaving the ask box restores the header and footer. If a tap caused the
  // blur, wait until it is released: restoring mid-tap would slide the button
  // being tapped out from under the finger and the tap would miss it.
  const pointerDownRef = useRef(false)
  const restorePendingRef = useRef(false)
  useEffect(() => {
    function handlePointerDown() {
      pointerDownRef.current = true
    }
    function handlePointerUp() {
      pointerDownRef.current = false
      if (!restorePendingRef.current) return
      // After this gesture's click has been dispatched.
      window.setTimeout(() => {
        if (!restorePendingRef.current) return
        restorePendingRef.current = false
        setAskFocused(false)
      }, 0)
    }
    document.addEventListener('pointerdown', handlePointerDown, true)
    document.addEventListener('pointerup', handlePointerUp, true)
    document.addEventListener('pointercancel', handlePointerUp, true)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown, true)
      document.removeEventListener('pointerup', handlePointerUp, true)
      document.removeEventListener('pointercancel', handlePointerUp, true)
    }
  }, [])
  const handleAskFocusChange = useCallback((focused: boolean) => {
    restorePendingRef.current = false
    if (focused || !pointerDownRef.current) {
      setAskFocused(focused)
    } else {
      restorePendingRef.current = true
    }
  }, [])

  // Focus rescue when the chips unmount while holding focus: on a switch to
  // Diagram view it goes to the Diagram toggle; when the window widens past md
  // (chips replaced by the desktop topic nav) it goes to the nav button for the
  // current topic. Runs after the commit, so isDesktopRef and the nav are current.
  const rescueChipFocus = () => {
    const target = isDesktopRef.current
      ? navRef.current?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]')
      : diagramButtonRef.current
    target?.focus()
  }

  const selectedQuestion = selectedNode ? questionForComponent(selectedNode) : null
  // Component questions always go to About This System's conversation.
  const systemMessages = conversations.system
  // The latest reply to the selected component's question: its answer, or the
  // friendly failure reply that follows any partial text.
  const questionIndex = selectedQuestion
    ? systemMessages.findLastIndex((message) => message.role === 'user' && message.content === selectedQuestion)
    : -1
  const selectedReply = questionIndex === -1 || questionIndex !== systemMessages.findLastIndex((message) => message.role === 'user')
    ? undefined
    : systemMessages.slice(questionIndex + 1).findLast((message) => message.role === 'assistant')
  const selectedAnswer = selectedReply ? selectedReply.content
    : selectedNode ? 'Waiting for the current answer…' : null

  const latestAnswer = messages.findLast((message) => message.role === 'assistant' && message.state !== 'pending')?.content || null

  const headerRef = useRef<HTMLElement>(null)
  const nameMeasureRef = useRef<HTMLSpanElement>(null)
  const actionsRef = useRef<HTMLDivElement>(null)
  const navRef = useRef<HTMLElement>(null)
  // One handler for the desktop nav and the mobile topic chips.
  const selectTopic = (next: Corpus) => {
    if (next === 'basel') { setCorpus('basel'); setSelectedNode(null); pendingComponentRef.current = null }
    else setCorpus('system')
  }
  const showFullName = useFullNameFits(headerRef, nameMeasureRef, isDesktop ? [actionsRef, navRef] : [actionsRef])

  // md+ only; below md the topic is chosen with the chips above the ask box
  // (Chat view only; Diagram view keeps the topic, it just hides the chips).
  const topicNav = (
      <nav ref={navRef} aria-label="Question topic" className="order-2 ml-4 flex items-center gap-2 text-xs">
        <button
          type="button"
          aria-pressed={corpus === 'basel'}
          onClick={() => selectTopic('basel')}
          className={`inline-flex items-center rounded-[3px] px-3 py-2 transition-colors hover:text-primary ${corpus === 'basel' ? 'text-cyan' : 'text-muted'}`}
        >
          About Basel
        </button>
        <span aria-hidden="true" className="text-hairline">|</span>
        <button
          type="button"
          aria-pressed={corpus === 'system'}
          onClick={() => selectTopic('system')}
          className={`inline-flex items-center rounded-[3px] px-3 py-2 transition-colors hover:text-primary ${corpus === 'system' ? 'text-cyan' : 'text-muted'}`}
        >
          About This System
        </button>
      </nav>
  )

  return (
    <div className={`flex h-dvh min-h-0 flex-col overflow-hidden bg-canvas font-mono text-primary ${shaking ? 'earthquake-shake' : ''}`}>
      <Collapsible open={headerOpen}>
      <header ref={headerRef} className={`relative flex shrink-0 md:min-h-[72px] flex-nowrap items-center gap-x-4 gap-y-0 border-b border-hairline px-4 py-2 md:gap-0 md:px-8 md:py-0 phone-landscape:py-0`}>
        {/*
          Below md: one row, [h1 ... envelope (Copy email), GitHub] (the topic
          chips sit above the ask box in Chat view). The envelope sits directly
          left of the GitHub icon as one right-aligned pair at every width. At
          md+ the row is [h1, nav ... envelope, GitHub] (visual order via
          `order-*`).
        */}
        <h1 className={`order-1 flex min-h-11 shrink-0 items-center whitespace-nowrap md:min-h-0 ${NAME_TEXT}`}>
          {/* Screen readers always get the full name; the visible text swaps to
              the short form only when the full one would push Contact/GitHub
              off the first row (measured, see useFullNameFits). */}
          <span className="sr-only">{FULL_NAME}</span>
          <span aria-hidden="true">{showFullName ? FULL_NAME : SHORT_NAME}</span>
        </h1>
        <span ref={nameMeasureRef} aria-hidden="true" className={`pointer-events-none invisible absolute left-0 top-0 whitespace-nowrap ${NAME_TEXT}`}>{FULL_NAME}</span>
          {isDesktop && topicNav}

        <div ref={actionsRef} data-auto-margin className="order-2 ml-auto flex items-center max-md:shrink-0 md:order-3 md:gap-3">
          <ContactReveal />
          <a
            href="https://github.com/hacka-tron/basel.engineering"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="View source on GitHub"
            className="-mr-2.5 flex size-11 items-center justify-center text-muted transition-colors hover:text-primary md:mr-0 md:size-auto"
          >
            <svg viewBox="0 0 16 16" className="size-6 sm:size-7 md:size-6" fill="currentColor" aria-hidden="true">
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
            </svg>
          </a>
        </div>
      </header>
      </Collapsible>

      <main className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[40%_60%]">
        <Chat
          corpus={corpus}
          messages={messages}
          isStreaming={isStreaming}
          onAsk={(question) => { setSelectedNode(null); handleAsk(question) }}
          onStop={handleStop}
          onNewChat={handleNewChat}
          onInputFocusChange={handleAskFocusChange}
          replacement={showDiagramView ? (
            <div className="flex min-h-0 flex-1 flex-col [&>section]:flex-1">
              <ArchitecturePanel
                portrait
                landscape={isPhoneLandscape}
                fitMinZoom={isPhoneLandscape ? 0.65 : 0.75}
                activeNode={activeNode}
                nodeCacheStatus={nodeCacheStatus}
                retrievedChunks={retrievedChunks}
                selectedNode={selectedNode}
                answerText={selectedAnswer}
                latestAnswer={latestAnswer}
                onContinueInChat={() => showMobileView('chat')}
                onInspect={handleInspectComponent}
                workerPods={shownWorkerPods}
                backlog={shownBacklog}
              />
            </div>
          ) : undefined}
          inputTopic={isDesktop || showDiagramView ? undefined : (
            <TopicChips value={corpus} options={TOPIC_CHIPS} onChange={selectTopic} onUnmountWithFocus={rescueChipFocus} />
          )}
          inputAccessory={
            <PipelineStrip
              view={showDiagramView ? 'diagram' : 'chat'}
              onViewChange={showMobileView}
              activeNode={activeNode}
              diagramButtonRef={diagramButtonRef}
            />
          }
        />
        {/*
          Each React Flow instance is mounted only where it is shown instead
          of CSS-hidden: it sets up a ResizeObserver, zoom/pan handlers, and an
          internal store on mount. Desktop mounts this one; below md the
          portrait diagram above mounts only in the diagram view. Only one
          ArchitecturePanel ever exists at a time.
        */}
        {isDesktop && <ArchitecturePanel fitMinZoom={0.65} activeNode={activeNode} nodeCacheStatus={nodeCacheStatus} retrievedChunks={retrievedChunks} selectedNode={selectedNode} onInspect={handleInspectComponent} workerPods={shownWorkerPods} backlog={shownBacklog} />}
      </main>

      <Collapsible open={!focusMode}>
      <StatsBar
        lastStats={lastStats}
        queriesServed={queriesServed}
        onStressTest={handleStressTestClick}
        onStressTap={onStressTap}
        stressTestCooldownSeconds={stressTest.cooldownSeconds}
        stressTestSubmitting={stressTest.isSubmitting}
        stressTestCapacity={stressTest.capacity}
        stressTestRealCooldownSeconds={stressTest.realCooldownSeconds}
        {...(isDesktop ? {} : {
          onNewChat: handleNewChat,
          newChatDisabled: isStreaming || messages.length === 0,
          topicLabel: corpus === 'basel' ? 'About Basel' : 'About This System',
        })}
      />
      </Collapsible>
    </div>
  )
}

export default App
