type StatsBarProps = {
  lastStats?: { totalMs: number; cacheStatus: 'hit' | 'miss'; tokensOut?: number } | null
  queriesServed?: number
}

function StatsBar({ lastStats, queriesServed = 0 }: StatsBarProps) {
  return (
    <footer className="flex h-[58px] shrink-0 items-center justify-between gap-4 overflow-x-auto border-t border-hairline bg-canvas px-4 text-xs text-muted md:gap-0 md:overflow-visible md:px-8">
      <div className="flex shrink-0 items-center gap-5">
        <span>last {lastStats ? `${lastStats.totalMs}ms` : '—ms'}</span>
        <span className="border-l border-hairline pl-5">cache {lastStats?.cacheStatus ?? '—'}</span>
        <span className="border-l border-hairline pl-5">{queriesServed} queries served</span>
      </div>
      <button
        type="button"
        disabled
        className="shrink-0 cursor-not-allowed rounded-[3px] border border-hairline px-4 py-2 text-muted"
      >
        Stress test
      </button>
    </footer>
  )
}

export default StatsBar
