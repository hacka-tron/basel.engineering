import { useEffect, useId, useRef, useState } from 'react'
import type { DemoCapacity } from '../hooks/useStressTest'
import { RABBIT_FACE_PATHS, TIGER_FACE_PATHS } from './capacityIcons'

type StatsBarProps = {
  lastStats?: { latencyMs: number; cacheStatus: 'hit' | 'miss'; tokensOut?: number } | null
  queriesServed?: number
  onStressTest?: () => void
  stressTestCooldownSeconds?: number | null
  stressTestSubmitting?: boolean
  stressTestCapacity?: DemoCapacity
  stressTestRealCooldownSeconds?: number | null
}

const LONG_PRESS_MS = 500
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
}: StatsBarProps) {
  const onCooldown = stressTestCooldownSeconds !== null
  // The API's `reason` (memory estimate vs. node allocatable) is for
  // debugging/logs; visitors only need to know which kind of demo they'll get.
  const realCooldownMinutes = stressTestRealCooldownSeconds === null
    ? null
    : Math.max(1, Math.ceil(stressTestRealCooldownSeconds / 60))
  const capacityLabel = stressTestCapacity.sufficient
    ? 'Ready for a real stress test. Clicking queues 300 jobs on the live cluster, and KEDA scales the retrieval workers from 1 up to 3 to drain them. Watch the pods and backlog on the Worker node. Tap to run.'
    : stressTestCapacity.realCooldown
      ? `A real stress test just ran, so the cluster is cooling down. For the next ${realCooldownMinutes} min, clicking plays a simulated version; no new jobs are queued. Tap to run.`
      : "There isn't enough cluster capacity for a real stress test right now, so clicking plays a simulated version instead. No jobs are queued and nothing scales. Tap to run."
  const disabled = !onStressTest || onCooldown || stressTestSubmitting

  const tooltipId = useId()
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

  useEffect(() => clearPressTimer, [])

  return (
    // Below md, gaps/padding/font are tightened (rather than left at the
    // desktop md: values) so this row fits at ~375px. It must not scroll
    // (overflow-x-auto would clip the absolutely-positioned capacity
    // tooltip, which opens upward out of the footer). "queries served" also drops to "queries" below `sm`,
    // since that's the single biggest chunk of text width at this size.
    <footer className="flex min-h-[58px] shrink-0 items-center pb-[env(safe-area-inset-bottom)] justify-between gap-2 border-t border-hairline bg-canvas px-3 text-[11px] text-muted sm:px-4 sm:text-xs md:gap-0 md:px-8">
      <div className="flex shrink-0 items-center gap-2 sm:gap-3 md:gap-5">
        <span>last {lastStats ? `${lastStats.latencyMs}ms` : '—ms'}</span>
        <span className="border-l border-hairline pl-2 sm:pl-3 md:pl-5">cache {lastStats?.cacheStatus ?? '—'}</span>
        <span className="border-l border-hairline pl-2 sm:pl-3 md:pl-5">
          {queriesServed} <span className="hidden sm:inline">queries served</span><span className="sm:hidden">queries</span>
        </span>
      </div>
      <div className="flex shrink-0 items-center">
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
          className={`group relative -mr-2 flex size-11 touch-manipulation select-none items-center justify-center rounded-full outline-none [-webkit-touch-callout:none] focus-visible:ring-1 focus-visible:ring-cyan sm:-mr-2.5 md:mr-0 ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}
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
            className={`pointer-events-none absolute bottom-full right-0 z-20 mb-1 w-60 sm:w-72 rounded-[3px] border border-hairline bg-panel p-2 text-left text-xs font-normal leading-relaxed text-primary shadow-lg group-hover:block group-focus-visible:block ${tooltipOpen ? 'block' : 'hidden'}`}
          >
            {capacityLabel}
          </span>
        </button>
      </div>
    </footer>
  )
}

export default StatsBar
