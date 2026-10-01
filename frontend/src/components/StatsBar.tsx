import { useEffect, useId, useState } from 'react'
import type { DemoCapacity } from '../hooks/useStressTest'
import { useLongPressTooltip } from '../hooks/useLongPressTooltip'
import { lastStatsDetails, lastStatsParts, type LastStats } from '../lib/lastStats'
import { RABBIT_FACE_PATHS, TIGER_FACE_PATHS } from './capacityIcons'

type StatsBarProps = {
  lastStats?: LastStats | null
  queriesServed?: number
  onStressTest?: () => void
  /** Called on every stress-test tap, including ones ignored while disabled (a run in flight or counting down). */
  onStressTap?: () => void
  stressTestCooldownSeconds?: number | null
  stressTestSubmitting?: boolean
  stressTestCapacity?: DemoCapacity
  stressTestRealCooldownSeconds?: number | null
  /** Below md the New chat control lives here, right-aligned beside the capacity icon. */
  onNewChat?: () => void
  newChatDisabled?: boolean
  /** Topic whose conversation New chat clears, e.g. "About Basel". */
  topicLabel?: string
}

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
  onStressTap,
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
  const { open: stressPressOpen, ref: stressPressRef, ...stressPressHandlers } = useLongPressTooltip<HTMLButtonElement>(() => { onStressTap?.(); if (!disabled) onStressTest?.() })
  const tooltipOpen = stressPressOpen
  const latencyTooltipId = useId()
  const { open: latencyPressOpen, ref: latencyPressRef, ...latencyPressHandlers } = useLongPressTooltip<HTMLButtonElement>()
  const { open: newChatPressOpen, ref: newChatPressRef, ...newChatPressHandlers } = useLongPressTooltip<HTMLButtonElement>(() => { if (!newChatDisabled) onNewChat?.() })

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

  // Escape dismisses the wide tooltip; blurring also drops the CSS focus-visible one.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== 'Escape') return
      setWideTooltipOpen(false)
      const active = document.activeElement as HTMLElement | null
      if (active?.closest?.('[data-stress-details]')) active.blur()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])

  const newChatTooltipId = useId()

  return (
    // Below md, gaps/padding/font are tightened (rather than left at the
    // desktop md: values) so this row fits at ~375px. It must not scroll
    // (overflow-x-auto would clip the absolutely-positioned tooltips, which
    // open upward out of the footer). "queries served" also drops to "queries" below `sm`,
    // since that's the single biggest chunk of text width at this size.
    <footer className="flex min-h-[58px] shrink-0 items-center justify-between gap-2 border-t border-hairline bg-canvas pb-[env(safe-area-inset-bottom)] pl-[max(1rem,env(safe-area-inset-left))] pr-[max(1rem,env(safe-area-inset-right))] text-[11px] text-muted sm:px-4 sm:text-xs md:gap-0 md:px-8">
      <div className="flex shrink-0 items-center gap-2 whitespace-nowrap sm:gap-3 md:gap-5">
        {/* Always one line: the stats are never wrapped or squeezed. The
            timing is a focusable control so hover, focus, and a long press
            (touch) explain what the number is; a tap does nothing. Like the
            capacity icon it brightens from muted on hover, keyboard focus,
            and press. */}
        <button
          type="button"
          ref={latencyPressRef}
          aria-label={`${latency.description}: ${latency.timing}${latency.cached ? ', cached' : ''}`}
          aria-describedby={latencyTooltipId}
          onClick={latencyPressHandlers.onClick}
          onPointerDown={latencyPressHandlers.onPointerDown}
          onPointerMove={latencyPressHandlers.onPointerMove}
          onPointerUp={latencyPressHandlers.onPointerUp}
          onPointerCancel={latencyPressHandlers.onPointerCancel}
          onContextMenu={latencyPressHandlers.onContextMenu}
          className="group relative -mx-2 flex min-h-11 cursor-help touch-manipulation select-none items-center px-2 leading-normal outline-none transition-colors [-webkit-touch-callout:none] hover:text-primary focus-visible:text-primary active:text-primary focus-visible:ring-1 focus-visible:ring-cyan md:min-h-0 md:px-0 md:mx-0"
        >
          <span>{latency.timing}</span>
          {latency.cached && <span className="ml-1">· cached</span>}
          <span
            id={latencyTooltipId}
            role="tooltip"
            className={`pointer-events-none absolute bottom-full left-0 z-20 mb-1 w-64 max-w-[calc(100vw-2rem)] space-y-1 whitespace-normal rounded-[3px] border border-hairline bg-panel p-2 text-left text-xs font-normal leading-relaxed text-primary shadow-lg [@media(hover:hover)]:group-hover:block group-focus-visible:block ${latencyPressOpen ? 'block' : 'hidden'}`}
          >
            {lastStatsDetails(lastStats ?? null).map((line) => <span key={line} className="block">{line}</span>)}
          </span>
        </button>
        <span className="border-l border-hairline pl-2 sm:pl-3 md:pl-5">
          {queriesServed} <span className="hidden sm:inline">{queriesServed === 1 ? 'query' : 'queries'} served</span><span className="sm:hidden">{queriesServed === 1 ? 'query' : 'queries'}</span>
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-1 sm:gap-2">
        {/* Below md: the "+" New chat icon sits at the right, in the same
            group as the capacity icon, so it comes after the stats in DOM and
            focus order. At md+ New chat lives under the ask box. */}
      {onNewChat && (
        // Same behaviour as the capacity icon: tap acts, press-and-hold shows
        // the details without acting. Always the "+" icon (no width switching).
        <button
          type="button"
          ref={newChatPressRef}
          aria-label="New chat"
          aria-disabled={newChatDisabled}
          aria-describedby={newChatTooltipId}
          onClick={newChatPressHandlers.onClick}
          onPointerDown={newChatPressHandlers.onPointerDown}
          onPointerMove={newChatPressHandlers.onPointerMove}
          onPointerUp={newChatPressHandlers.onPointerUp}
          onPointerCancel={newChatPressHandlers.onPointerCancel}
          onContextMenu={newChatPressHandlers.onContextMenu}
          className={`group relative flex size-11 shrink-0 touch-manipulation select-none items-center justify-center rounded-full outline-none [-webkit-touch-callout:none] focus-visible:ring-1 focus-visible:ring-cyan md:hidden ${newChatDisabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}
        >
          <span
            aria-hidden="true"
            className={`flex items-center justify-center text-muted transition-colors group-hover:text-primary group-focus-visible:text-primary group-active:text-primary ${newChatDisabled ? 'opacity-40' : ''}`}
          >
            <svg viewBox="0 0 16 16" className="size-6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"><path d="M8 3v10M3 8h10" /></svg>
          </span>
          <span
            id={newChatTooltipId}
            role="tooltip"
            className={`pointer-events-none absolute bottom-full right-0 z-20 mb-1 w-56 max-w-[calc(100vw-2rem)] whitespace-normal rounded-[3px] border border-hairline bg-panel p-2 text-left text-xs font-normal leading-relaxed text-primary shadow-lg group-hover:block group-focus-visible:block ${newChatPressOpen ? 'block' : 'hidden'}`}
          >
            New chat: clears the {topicLabel} conversation only. Chats are saved in this browser.
          </span>
        </button>
      )}

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
          onClick={() => { onStressTap?.(); if (!disabled) onStressTest?.() }}
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
          ref={stressPressRef}
          onClick={stressPressHandlers.onClick}
          onPointerDown={stressPressHandlers.onPointerDown}
          onPointerMove={stressPressHandlers.onPointerMove}
          onPointerUp={stressPressHandlers.onPointerUp}
          onPointerCancel={stressPressHandlers.onPointerCancel}
          onContextMenu={stressPressHandlers.onContextMenu}
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
