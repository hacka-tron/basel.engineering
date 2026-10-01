/**
 * The footer's latency readout for the last answer.
 *
 * The number is client-measured time to first token: from pressing Send to
 * the first answer token arriving over the network. It excludes how long the
 * rest of the answer took to generate and the reveal animation's pacing.
 * When no token arrived (budget reached or the LLM switched off, so only the
 * sources came back) there is no first-token time to show, so the readout
 * says "total" and shows the server's whole-request time instead of passing
 * that off as a first-token time.
 */
export type LastStats = {
  firstTokenMs: number | null
  totalMs: number
  cacheStatus: 'hit' | 'miss'
}

/** The readout split for layout: the timing, and whether it was a cache hit. */
export function lastStatsParts(stats: LastStats | null): { timing: string; cached: boolean } {
  if (!stats) return { timing: 'first token —', cached: false }
  if (stats.firstTokenMs === null) return { timing: `total ${stats.totalMs}ms`, cached: false }
  return { timing: `first token ${stats.firstTokenMs}ms`, cached: stats.cacheStatus === 'hit' }
}

export function lastStatsLabel(stats: LastStats | null): string {
  const { timing, cached } = lastStatsParts(stats)
  return cached ? `${timing} · cached` : timing
}
