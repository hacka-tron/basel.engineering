import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type { DemoCapacity } from '../hooks/useStressTest'
import { newChatFit, type NewChatFit } from '../lib/footerFit'
import { lastStatsParts, type LastStats } from '../lib/lastStats'
import { RABBIT_FACE_PATHS, TIGER_FACE_PATHS } from './capacityIcons'

type StatsBarProps = {
  lastStats?: LastStats | null
  queriesServed?: number
  onStressTest?: () => void
  stressTestCooldownSeconds?: number | null
  stressTestSubmitting?: boolean
  stressTestCapacity?: DemoCapacity
  stressTestRealCooldownSeconds?: number | null
  /** Below md the New chat control lives here, right after the stats. */
  onNewChat?: () => void
  newChatDisabled?: boolean
  /** Topic whose conversation New chat clears, e.g. "About Basel". */
  topicLabel?: string
}

const LONG_PRESS_MS = 500
// The square "+" New chat button (w-8).
const COMPACT_NEW_CHAT_PX = 32
const LONG_PRESS_SLOP_PX = 10
const TOOLTIP_AUTO_HIDE_MS = 4000

/**
 * Capacity icon: muted at rest and brightening on hover/focus/press of its
 * `group` parent, matching the header's GitHub mark. Tiger = room for a real
 * stress test; bunny = simulated demo only.
 */
function Avatar({ paths, dimmed }: { paths: string[]; dimmed: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`flex items-center justify-center text-muted transition-colors group-hover:text-primary group-focus-visible:text-primary group-active:text-primary ${dimmed ? 'opacity-40' : ''}`}
    >
      <svg viewBox="0 0 32 32" className="size-6 sm:size-7" fill="currentColor">
        {paths.map((d) => <path key={d.slice(0, 24)} d={d} />)}
      </svg>
    </span>
  )
}

function StatsBar({
  lastStats,
  queriesServed = 0,
  onStressTest,
  stressTestCooldownSeconds = null,
  stressTestSubmitting = false,
  stressTestCapacity = { sufficient: false, reason: 'Checking cluster capacity…' },
  stressTestRealCooldownSeconds = null,
  onNewChat,
  newChatDisabled = false,
  topicLabel = 'this',
}: StatsBarProps) {
  const onCooldown = stressTestCooldownSeconds !== null
  // The API's `reason` (memory estimate vs. node allocatable) is for
  // debugging/logs; visitors only need to know which kind of demo they'll get.
  const realCooldownMinutes = stressTestRealCooldownSeconds === null
    ? null
    : Math.max(1, Math.ceil(stressTestRealCooldownSeconds / 60))
  const capacityBase = stressTestCapacity.sufficient
    ? 'Ready for a real stress test. Clicking queues 300 jobs on the live cluster, and KEDA scales the retrieval workers from 1 up to 3 to drain them. Watch the pods and backlog on the Worker node.'
    : stressTestCapacity.realCooldown
      ? `A real stress test just ran, so the cluster is cooling down. For the next ${realCooldownMinutes} min, clicking plays a simulated version; no new jobs are queued.`
      : "There isn't enough cluster capacity for a real stress test right now, so clicking plays a simulated version instead. No jobs are queued and nothing scales."
  // Narrow: the icon is the button. Wide: the labelled button runs it.
  const capacityLabel = `${capacityBase} Tap to run.`
  const wideCapacityLabel = `${capacityBase} Use the Stress test button to run it.`
  const disabled = !onStressTest || onCooldown || stressTestSubmitting
  const latency = lastStatsParts(lastStats ?? null)

  const tooltipId = useId()
  const wideTooltipId = useId()
  const [wideTooltipOpen, setWideTooltipOpen] = useState(false)
  const [tooltipOpen, setTooltipOpen] = useState(false)
  const pressTimerRef = useRef<number | null>(null)
  // One gesture state decides whether the release runs the test:
  // idle -> pressing -> longpress | cancelled -> (next pointerdown) idle.
  // Only a gesture that is still `idle`/`pressing` at click time runs it.
  // Keyboard clicks (detail === 0) have no pointer gesture and always run.
  const gestureRef = useRef<'idle' | 'pressing' | 'longpress' | 'cancelled'>('idle')
  const activePointersRef = useRef(new Set<number>())
  const pressStartRef = useRef<{ x: number; y: number } | null>(null)

  function clearPressTimer() {
    if (pressTimerRef.current !== null) {
      window.clearTimeout(pressTimerRef.current)
      pressTimerRef.current = null
    }
  }

  function cancelGesture() {
    clearPressTimer()
    if (gestureRef.current === 'pressing') gestureRef.current = 'cancelled'
  }

  // Touch/pen only: a ~500ms hold opens the details instead of running the
  // test. Mouse users get the hover tooltip, so they never need a long-press.
  // A second finger, a cancel, or drifting past the slop cancels the gesture.
  function handlePointerDown(e: React.PointerEvent) {
    clearPressTimer()
    if (e.pointerType === 'mouse') {
      activePointersRef.current.clear()
      gestureRef.current = 'idle'
      return
    }
    activePointersRef.current.add(e.pointerId)
    if (activePointersRef.current.size > 1) {
      gestureRef.current = 'cancelled'
      return
    }
    gestureRef.current = 'pressing'
    pressStartRef.current = { x: e.clientX, y: e.clientY }
    pressTimerRef.current = window.setTimeout(() => {
      pressTimerRef.current = null
      gestureRef.current = 'longpress'
      setTooltipOpen(true)
    }, LONG_PRESS_MS)
  }

  function handlePointerMove(e: React.PointerEvent) {
    const start = pressStartRef.current
    if (!start || gestureRef.current !== 'pressing') return
    if (Math.hypot(e.clientX - start.x, e.clientY - start.y) > LONG_PRESS_SLOP_PX) cancelGesture()
  }

  function handlePointerUp(e: React.PointerEvent) {
    activePointersRef.current.delete(e.pointerId)
    clearPressTimer()
  }

  function handlePointerCancel(e: React.PointerEvent) {
    activePointersRef.current.delete(e.pointerId)
    cancelGesture()
    gestureRef.current = 'cancelled'
  }

  function handleClick(e: React.MouseEvent) {
    const state = gestureRef.current
    gestureRef.current = 'idle'
    if (e.detail !== 0 && (state === 'longpress' || state === 'cancelled')) return
    setTooltipOpen(false)
    if (!disabled) onStressTest?.()
  }

  useEffect(() => {
    if (!tooltipOpen) return
    const hide = window.setTimeout(() => setTooltipOpen(false), TOOLTIP_AUTO_HIDE_MS)
    function onDocPointerDown(e: PointerEvent) {
      if (!(e.target as Element | null)?.closest?.('[data-stress-button]')) setTooltipOpen(false)
    }
    document.addEventListener('pointerdown', onDocPointerDown)
    return () => {
      window.clearTimeout(hide)
      document.removeEventListener('pointerdown', onDocPointerDown)
    }
  }, [tooltipOpen])

  useEffect(() => {
    if (!wideTooltipOpen) return
    const hide = window.setTimeout(() => setWideTooltipOpen(false), TOOLTIP_AUTO_HIDE_MS)
    function onDocPointerDown(e: PointerEvent) {
      if (!(e.target as Element | null)?.closest?.('[data-stress-details]')) setWideTooltipOpen(false)
    }
    document.addEventListener('pointerdown', onDocPointerDown)
    return () => {
      window.clearTimeout(hide)
      document.removeEventListener('pointerdown', onDocPointerDown)
    }
  }, [wideTooltipOpen])

  // Escape dismisses either tooltip; blurring also drops the CSS focus-visible one.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== 'Escape') return
      setTooltipOpen(false)
      setWideTooltipOpen(false)
      const active = document.activeElement as HTMLElement | null
      if (active?.closest?.('[data-stress-details], [data-stress-button]')) active.blur()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])

  useEffect(() => clearPressTimer, [])

  // Labelled "New chat" when the measured free width allows it, otherwise a
  // compact "+" with the same action and tooltip (lib/footerFit.ts). The stats
  // never wrap or shrink to make room.
  const newChatTooltipId = useId()
  const footerRef = useRef<HTMLElement>(null)
  const statsRef = useRef<HTMLDivElement>(null)
  const stressRef = useRef<HTMLDivElement>(null)
  const newChatLabelRef = useRef<HTMLSpanElement>(null)
  const prefixMeasureRef = useRef<HTMLSpanElement>(null)
  const [fit, setFit] = useState<NewChatFit>('label')
  const fitRef = useRef(fit)
  const newChatCompact = fit !== 'label'
  const timingPrefix = latency.timing.startsWith('first token ') ? 'first token ' : ''
  const hasTimingPrefix = timingPrefix !== ''
  const hasNewChat = onNewChat !== undefined
  useLayoutEffect(() => {
    const footer = footerRef.current
    const stats = statsRef.current
    if (!hasNewChat || !footer || !stats) return
    function measure() {
      if (!footer || !stats || !stressRef.current || !newChatLabelRef.current || !prefixMeasureRef.current) return
      const style = getComputedStyle(footer)
      const prefix = hasTimingPrefix ? prefixMeasureRef.current.getBoundingClientRect().width : 0
      // While tight the prefix is not drawn; add it back so the choice is
      // always made against the full-length stats.
      const shownStats = stats.getBoundingClientRect().width
      const next = newChatFit({
        available: footer.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
        stats: fitRef.current === 'tight' ? shownStats + prefix : shownStats,
        prefix,
        stressControl: stressRef.current.getBoundingClientRect().width,
        label: newChatLabelRef.current.getBoundingClientRect().width,
        compact: COMPACT_NEW_CHAT_PX,
      })
      fitRef.current = next
      setFit(next)
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(footer)
    observer.observe(stats)
    if (stressRef.current) observer.observe(stressRef.current)
    return () => observer.disconnect()
  }, [hasNewChat, hasTimingPrefix])

  return (
    // Below md, gaps/padding/font are tightened (rather than left at the
    // desktop md: values) so this row fits at ~375px. It must not scroll
    // (overflow-x-auto would clip the absolutely-positioned capacity
    // tooltip, which opens upward out of the footer). "queries served" also drops to "queries" below `sm`,
    // since that's the single biggest chunk of text width at this size.
    <footer ref={footerRef} className="flex min-h-[58px] shrink-0 items-center justify-between gap-2 border-t border-hairline bg-canvas pb-[env(safe-area-inset-bottom)] pl-[max(1rem,env(safe-area-inset-left))] pr-[max(1rem,env(safe-area-inset-right))] text-[11px] text-muted sm:px-4 sm:text-xs md:gap-0 md:px-8">
      {/* Below md: stats then New chat, as one group. At md+ the wrapper
          dissolves (display: contents) and New chat lives under the ask box. */}
      <div className="flex min-w-0 items-center gap-3 md:contents">
      <div ref={statsRef} className="flex shrink-0 items-center gap-2 whitespace-nowrap sm:gap-3 md:gap-5">
        {/* Always one line: the stats are never wrapped or squeezed. */}
        <span className="flex leading-normal" title={fit === 'tight' ? latency.timing : undefined}>
          {hasNewChat && timingPrefix ? (
            <span>
              <span className={fit === 'tight' ? 'sr-only' : undefined}>{timingPrefix}</span>
              {latency.timing.slice(timingPrefix.length)}
            </span>
          ) : (
            <span>{latency.timing}</span>
          )}
          {latency.cached && <span className="ml-1">· cached</span>}
        </span>
        <span className="border-l border-hairline pl-2 sm:pl-3 md:pl-5">
          {queriesServed} <span className="hidden sm:inline">queries served</span><span className="sm:hidden">queries</span>
        </span>
      </div>
      {onNewChat && (
        <button
          type="button"
          onClick={onNewChat}
          disabled={newChatDisabled}
          aria-label={newChatCompact ? 'New chat' : undefined}
          aria-describedby={newChatTooltipId}
          className={`group relative inline-flex h-8 shrink-0 items-center justify-center whitespace-nowrap rounded-[3px] border border-hairline text-xs text-primary outline-none transition-colors after:absolute after:-inset-y-1.5 after:content-[''] hover:border-cyan hover:text-cyan focus-visible:ring-1 focus-visible:ring-cyan disabled:cursor-not-allowed disabled:text-muted disabled:hover:border-hairline md:hidden ${newChatCompact ? 'w-8 after:-inset-x-1.5' : 'px-2.5 after:-inset-x-1'}`}
        >
          {newChatCompact ? (
            <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>
          ) : 'New chat'}
          <span
            id={newChatTooltipId}
            role="tooltip"
            className="pointer-events-none absolute bottom-full right-0 z-20 mb-2 hidden w-56 max-w-[calc(100vw-2rem)] whitespace-normal rounded-[3px] border border-hairline bg-panel p-2 text-left text-xs font-normal leading-relaxed text-primary shadow-lg [@media(hover:hover)]:group-hover:block group-focus-visible:block"
          >
            New chat: clears the {topicLabel} conversation only. Chats are saved in this browser.
          </span>
        </button>
      )}
      {onNewChat && (
        // Measures the labelled button so the choice above never depends on
        // what is currently rendered.
        <>
          <span ref={newChatLabelRef} aria-hidden="true" className="pointer-events-none invisible absolute left-0 top-0 inline-flex h-8 items-center whitespace-nowrap border px-2.5 text-xs md:hidden">New chat</span>
          <span ref={prefixMeasureRef} aria-hidden="true" className="pointer-events-none invisible absolute left-0 top-0 whitespace-pre md:hidden">first token </span>
        </>
      )}
      </div>
      <div ref={stressRef} className="flex shrink-0 items-center sm:gap-2">
        {/* >= sm: a labelled button runs the test; the icon beside it is
            details-only (hover/focus shows the tooltip, click/tap toggles it),
            so there is exactly one control per action. Both are display:none
            below sm, so they leave the tab order and the accessibility tree. */}
        <button
          type="button"
          aria-disabled={disabled}
          aria-describedby={wideTooltipId}
          aria-label={onCooldown
            ? `Stress test on cooldown, ${stressTestCooldownSeconds}s remaining`
            : `Run stress test (${stressTestCapacity.sufficient ? 'real' : 'simulated'})`}
          onClick={() => { if (!disabled) onStressTest?.() }}
          className={`hidden min-h-11 shrink-0 touch-manipulation rounded-[3px] border px-4 py-2 outline-none transition-colors focus-visible:ring-1 focus-visible:ring-cyan sm:inline-block md:min-h-0 ${
            disabled
              ? 'cursor-not-allowed border-hairline text-muted'
              : 'cursor-pointer border-hairline text-primary hover:border-cyan hover:text-cyan'
          }`}
        >
          {onCooldown ? `Stress test (${stressTestCooldownSeconds}s)` : 'Stress test'}
        </button>
        <button
          type="button"
          aria-label={`Cluster capacity: ${stressTestCapacity.sufficient ? 'real' : 'simulated'} stress test. Show details`}
          aria-describedby={wideTooltipId}
          aria-expanded={wideTooltipOpen}
          data-stress-details
          onClick={() => setWideTooltipOpen((v) => !v)}
          onBlur={() => setWideTooltipOpen(false)}
          className="group relative hidden size-11 shrink-0 cursor-help select-none items-center justify-center rounded-full outline-none focus-visible:ring-1 focus-visible:ring-cyan sm:flex"
        >
          <Avatar paths={stressTestCapacity.sufficient ? TIGER_FACE_PATHS : RABBIT_FACE_PATHS} dimmed={false} />
          <span
            id={wideTooltipId}
            role="tooltip"
            className={`pointer-events-none absolute bottom-full right-0 z-20 mb-1 w-72 rounded-[3px] border border-hairline bg-panel p-2 text-left text-xs font-normal leading-relaxed text-primary shadow-lg [@media(hover:hover)]:group-hover:block group-focus-visible:block ${wideTooltipOpen ? 'block' : 'hidden'}`}
          >
            {wideCapacityLabel}
          </span>
        </button>
        {/* < sm: the icon IS the button (tap runs, press-and-hold shows details). */}
        <button
          type="button"
          aria-disabled={disabled}
          aria-label={onCooldown
            ? `Stress test on cooldown, ${stressTestCooldownSeconds}s remaining`
            : `Run stress test (${stressTestCapacity.sufficient ? 'real' : 'simulated'})`}
          aria-describedby={tooltipId}
          data-stress-button
          onClick={handleClick}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerCancel}
          onContextMenu={(e) => e.preventDefault()}
          className={`group relative -mr-2.5 flex size-11 touch-manipulation select-none items-center justify-center rounded-full outline-none [-webkit-touch-callout:none] focus-visible:ring-1 focus-visible:ring-cyan sm:hidden ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}
        >
          <Avatar paths={stressTestCapacity.sufficient ? TIGER_FACE_PATHS : RABBIT_FACE_PATHS} dimmed={disabled} />
          {onCooldown && (
            <span aria-hidden="true" className="absolute bottom-0.5 right-0.5 text-[11px] leading-none tabular-nums text-cyan">
              {stressTestCooldownSeconds}
            </span>
          )}
          <span
            id={tooltipId}
            role="tooltip"
            className={`pointer-events-none absolute bottom-full right-0 z-20 mb-1 w-60 rounded-[3px] border border-hairline bg-panel p-2 text-left text-xs font-normal leading-relaxed text-primary shadow-lg group-hover:block group-focus-visible:block ${tooltipOpen ? 'block' : 'hidden'}`}
          >
            {capacityLabel}
          </span>
        </button>
      </div>
    </footer>
  )
}

export default StatsBar
