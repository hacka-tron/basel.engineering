import type { DemoCapacity } from '../hooks/useStressTest'

type StatsBarProps = {
  lastStats?: { latencyMs: number; cacheStatus: 'hit' | 'miss'; tokensOut?: number } | null
  queriesServed?: number
  onStressTest?: () => void
  stressTestCooldownSeconds?: number | null
  stressTestSubmitting?: boolean
  stressTestCapacity?: DemoCapacity
}

/** White bunny avatar; dim like the header's GitHub mark at rest, brightens on hover/focus of its `group` parent. */
function BunnyAvatar() {
  return (
    <span
      aria-hidden="true"
      className="flex size-6 items-center justify-center rounded-full border border-hairline bg-panel text-muted transition-colors group-hover:border-primary group-hover:text-primary group-focus:border-primary group-focus:text-primary"
    >
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <ellipse cx="8.5" cy="6" rx="2.2" ry="5" transform="rotate(-8 8.5 6)" />
        <ellipse cx="15.5" cy="6" rx="2.2" ry="5" transform="rotate(8 15.5 6)" />
        <ellipse cx="12" cy="15" rx="6.5" ry="6" />
        <circle cx="9.6" cy="14" r="0.9" className="fill-panel" />
        <circle cx="14.4" cy="14" r="0.9" className="fill-panel" />
        <path d="M11 16.3h2l-1 1.1z" className="fill-panel" />
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
          aria-label={stressTestCapacity.reason}
        >
          {stressTestCapacity.sufficient ? (
            <span aria-hidden="true" className="text-base">🦁</span>
          ) : (
            <BunnyAvatar />
          )}
          <span className="pointer-events-none absolute bottom-full right-0 z-20 mb-2 hidden w-64 rounded-[3px] border border-hairline bg-panel p-2 text-left text-xs leading-relaxed text-primary shadow-lg group-hover:block group-focus:block">
            {stressTestCapacity.sufficient ? 'There is room for a real worker scale-up. ' : 'Visual demo only; no jobs will be queued. '}
            {stressTestCapacity.reason}
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
