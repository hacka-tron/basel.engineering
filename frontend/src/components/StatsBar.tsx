type StatsBarProps = {
  lastStats?: { totalMs: number; cacheStatus: 'hit' | 'miss'; tokensOut?: number } | null
  queriesServed?: number
}

function StatsBar({ lastStats, queriesServed = 0 }: StatsBarProps) {
  return (
    // Below md, gaps/padding are tightened (rather than left at the desktop
    // md: values) so this row fits without scrolling down to a ~375px
    // phone — overflow-x-auto remains only as a fallback for edge cases
    // (very narrow devices, unusually large numbers), not a substitute for
    // sane spacing. "queries served" also drops to "queries" below `sm`,
    // since that's the single biggest chunk of text width at this size.
    <footer className="flex h-[58px] shrink-0 items-center justify-between gap-3 overflow-x-auto border-t border-hairline bg-canvas px-4 text-xs text-muted md:gap-0 md:overflow-visible md:px-8">
      <div className="flex shrink-0 items-center gap-3 md:gap-5">
        <span>last {lastStats ? `${lastStats.totalMs}ms` : '—ms'}</span>
        <span className="border-l border-hairline pl-3 md:pl-5">cache {lastStats?.cacheStatus ?? '—'}</span>
        <span className="border-l border-hairline pl-3 md:pl-5">
          {queriesServed} <span className="hidden sm:inline">queries served</span><span className="sm:hidden">queries</span>
        </span>
      </div>
      <button
        type="button"
        disabled
        className="shrink-0 cursor-not-allowed rounded-[3px] border border-hairline px-3 py-2 text-muted md:px-4"
      >
        Stress test
      </button>
    </footer>
  )
}

export default StatsBar
