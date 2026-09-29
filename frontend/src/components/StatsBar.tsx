function StatsBar() {
  return (
    <footer className="flex h-[58px] shrink-0 items-center justify-between gap-4 overflow-x-auto border-t border-hairline bg-canvas px-4 text-xs text-muted md:gap-0 md:overflow-visible md:px-8">
      <div className="flex shrink-0 items-center gap-5">
        <span>p50 —ms</span>
        <span className="border-l border-hairline pl-5">cache hit —%</span>
        <span className="border-l border-hairline pl-5">— queries served</span>
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
