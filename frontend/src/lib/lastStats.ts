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
 *
 * The visible text is just the number (`312ms`); what it measures lives in
 * `description`, used for the tooltip and screen readers.
 */
export type LastStats = {
  firstTokenMs: number | null
  totalMs: number
  cacheStatus: 'hit' | 'miss'
}

/** The readout split for layout: the timing, and whether it was a cache hit. */
export function lastStatsParts(stats: LastStats | null): { timing: string; cached: boolean; description: string } {
  const firstToken = 'Time to first token'
  if (!stats) return { timing: '—', cached: false, description: firstToken }
  if (stats.firstTokenMs === null) return { timing: `total ${stats.totalMs}ms`, cached: false, description: 'Total request time (no answer text was generated)' }
  return { timing: `${stats.firstTokenMs}ms`, cached: stats.cacheStatus === 'hit', description: firstToken }
}

export function lastStatsLabel(stats: LastStats | null): string {
  const { timing, cached } = lastStatsParts(stats)
  return cached ? `${timing} · cached` : timing
}

/** Short tooltip text (two or three lines) explaining the readout. */
export function lastStatsDetails(stats: LastStats | null): string[] {
  if (!stats) return ['Time to first token: how long until the first word of an answer arrives. Ask a question to see yours.']
  if (stats.firstTokenMs === null) {
    return [`Total time: ${stats.totalMs}ms.`, 'No answer text was generated, so there is no time to first token.']
  }
  const lines = [`Time to first token: ${stats.firstTokenMs}ms. How long until the first word of the answer arrived.`]
  lines.push(stats.cacheStatus === 'hit' ? 'Served from the answer cache.' : `Whole answer: ${stats.totalMs}ms.`)
  return lines
}
