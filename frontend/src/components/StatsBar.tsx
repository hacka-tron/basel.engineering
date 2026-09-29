function StatsBar() {
  return (
    <footer className="flex h-[58px] shrink-0 items-center justify-between border-t border-hairline bg-canvas px-8 text-xs text-muted">
      <div className="flex items-center gap-5">
        <span>p50 —ms</span>
        <span className="border-l border-hairline pl-5">cache hit —%</span>
        <span className="border-l border-hairline pl-5">— queries served</span>
      </div>
      <button
        type="button"
        disabled
        className="cursor-not-allowed rounded-[3px] border border-hairline px-4 py-2 text-muted"
      >
        Stress test
      </button>
    </footer>
  )
}

export default StatsBar
