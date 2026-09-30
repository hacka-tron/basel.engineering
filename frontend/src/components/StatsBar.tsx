import type { DemoCapacity } from '../hooks/useStressTest'
import { RABBIT_FACE_PATHS, TIGER_FACE_PATHS } from './capacityIcons'

type StatsBarProps = {
  lastStats?: { latencyMs: number; cacheStatus: 'hit' | 'miss'; tokensOut?: number } | null
  queriesServed?: number
  onStressTest?: () => void
  stressTestCooldownSeconds?: number | null
  stressTestSubmitting?: boolean
  stressTestCapacity?: DemoCapacity
}

/**
 * Capacity-status icon: muted at rest and brightening on hover/focus of its
 * `group` parent, matching the header's GitHub mark. Tiger = room for a real
 * stress test; bunny = simulated demo only.
 */
function Avatar({ paths }: { paths: string[] }) {
  return (
    <span
      aria-hidden="true"
      className="flex items-center justify-center text-muted transition-colors group-hover:text-primary group-focus:text-primary"
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
}: StatsBarProps) {
  const onCooldown = stressTestCooldownSeconds !== null
  // The API's `reason` (memory estimate vs. node allocatable) is for
  // debugging/logs; visitors only need to know which kind of demo they'll get.
  const capacityLabel = stressTestCapacity.sufficient
    ? 'Ready for a real stress test. Clicking queues 300 jobs on the live cluster, and KEDA scales the retrieval workers from 1 up to 5 to drain them. Watch the pods and backlog on the Worker node.'
    : "There isn't enough cluster capacity for a real stress test right now, so clicking plays a simulated version instead. No jobs are queued and nothing scales."
  const disabled = !onStressTest || onCooldown || stressTestSubmitting

  return (
    // Below md, gaps/padding/font are tightened (rather than left at the
    // desktop md: values) so this row fits at ~375px. It must not scroll
    // (overflow-x-auto would clip the absolutely-positioned capacity
    // tooltip, which opens upward out of the footer). "queries served" also drops to "queries" below `sm`,
    // since that's the single biggest chunk of text width at this size.
    <footer className="flex h-[58px] shrink-0 items-center justify-between gap-2 border-t border-hairline bg-canvas px-3 text-[11px] text-muted sm:px-4 sm:text-xs md:gap-0 md:px-8">
      <div className="flex shrink-0 items-center gap-2 sm:gap-3 md:gap-5">
        <span>last {lastStats ? `${lastStats.latencyMs}ms` : '—ms'}</span>
        <span className="border-l border-hairline pl-2 sm:pl-3 md:pl-5">cache {lastStats?.cacheStatus ?? '—'}</span>
        <span className="border-l border-hairline pl-2 sm:pl-3 md:pl-5">
          {queriesServed} <span className="hidden sm:inline">queries served</span><span className="sm:hidden">queries</span>
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
        <span
          className="group relative inline-flex cursor-help rounded-full outline-none focus-visible:ring-1 focus-visible:ring-cyan"
          tabIndex={0}
          role="status"
          aria-label={capacityLabel}
        >
          <Avatar paths={stressTestCapacity.sufficient ? TIGER_FACE_PATHS : RABBIT_FACE_PATHS} />
          <span className="pointer-events-none absolute bottom-full right-0 z-20 mb-2 hidden w-60 sm:w-72 rounded-[3px] border border-hairline bg-panel p-2 text-left text-xs leading-relaxed text-primary shadow-lg group-hover:block group-focus:block">
            {capacityLabel}
          </span>
        </span>
      <button
        type="button"
        disabled={disabled}
        onClick={onStressTest}
        aria-label={onCooldown ? `Stress test on cooldown, ${stressTestCooldownSeconds}s remaining` : 'Stress test'}
        className={`shrink-0 rounded-[3px] border px-3 py-2 transition-colors sm:px-4 ${
          disabled
            ? 'cursor-not-allowed border-hairline text-muted'
            : 'border-hairline text-primary hover:border-cyan hover:text-cyan'
        }`}
      >
        {onCooldown ? (
          <>
            <span className="hidden sm:inline">Stress test (</span>
            {stressTestCooldownSeconds}s
            <span className="hidden sm:inline">)</span>
          </>
        ) : (
          'Stress test'
        )}
      </button>
      </div>
    </footer>
  )
}

export default StatsBar
