import type { DemoCapacity } from '../hooks/useStressTest'

type StatsBarProps = {
  lastStats?: { latencyMs: number; cacheStatus: 'hit' | 'miss'; tokensOut?: number } | null
  queriesServed?: number
  onStressTest?: () => void
  stressTestCooldownSeconds?: number | null
  stressTestSubmitting?: boolean
  stressTestCapacity?: DemoCapacity
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
    // Below md, gaps/padding are tightened (rather than left at the desktop
    // md: values) so this row fits without scrolling down to a ~375px
    // phone — overflow-x-auto remains only as a fallback for edge cases
    // (very narrow devices, unusually large numbers), not a substitute for
    // sane spacing. "queries served" also drops to "queries" below `sm`,
    // since that's the single biggest chunk of text width at this size.
    <footer className="flex h-[58px] shrink-0 items-center justify-between gap-3 overflow-x-auto border-t border-hairline bg-canvas px-4 text-xs text-muted md:gap-0 md:overflow-visible md:px-8">
      <div className="flex shrink-0 items-center gap-3 md:gap-5">
        <span>last {lastStats ? `${lastStats.latencyMs}ms` : '—ms'}</span>
        <span className="border-l border-hairline pl-3 md:pl-5">cache {lastStats?.cacheStatus ?? '—'}</span>
        <span className="border-l border-hairline pl-3 md:pl-5">
          {queriesServed} <span className="hidden sm:inline">queries served</span><span className="sm:hidden">queries</span>
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <span className="group relative inline-flex cursor-help" tabIndex={0} role="status" aria-label={stressTestCapacity.reason}>
          <span aria-hidden="true" className="text-base">{stressTestCapacity.sufficient ? '🦁' : '🐰'}</span>
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
        className={`shrink-0 rounded-[3px] border px-4 py-2 transition-colors ${
          disabled
            ? 'cursor-not-allowed border-hairline text-muted'
            : 'border-hairline text-primary hover:border-cyan hover:text-cyan'
        }`}
      >
        {onCooldown ? `Stress test (${stressTestCooldownSeconds}s)` : 'Stress test'}
      </button>
      </div>
    </footer>
  )
}

export default StatsBar
