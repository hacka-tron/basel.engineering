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
 * `description`, used for the tooltip and screen readers. Below `sm` the
 * footer shows `short`, the same time in at most five characters (`1.8s`).
 */
export type LastStats = {
  firstTokenMs: number | null
  totalMs: number
  cacheStatus: 'hit' | 'miss'
}

/**
 * The phone reading (below `sm`): at most five characters for any value, so
 * the footer fits beside the Open to work item, New chat and the capacity
 * icon at 280–393px (a "total 12345ms" reading overflowed at 280px).
 * Under a second: whole ms ("312ms"); then tenths of a second up to 99.9s
 * ("1.8s", "12.3s"); then whole seconds ("100s"), capped at "999s+".
 */
export function compactDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '—'
  const whole = Math.round(ms)
  if (whole < 1000) return `${whole}ms`
  const tenths = Math.round(whole / 100)
  if (tenths < 1000) return `${(tenths / 10).toFixed(1)}s`
  const seconds = Math.round(whole / 1000)
  return seconds < 1000 ? `${seconds}s` : '999s+'
}

/**
 * The visible timing, its phone form and the description used for the
 * tooltip and screen readers. `short` drops the word "total" for room; the
 * accessible name and tooltip still say what the number is. Cache hits are
 * not marked here; only the tooltip mentions them.
 */
export function lastStatsParts(stats: LastStats | null): { timing: string; short: string; description: string } {
  const firstToken = 'Time to first token'
  if (!stats) return { timing: '—', short: '—', description: firstToken }
  if (stats.firstTokenMs === null) {
    return { timing: `total ${stats.totalMs}ms`, short: compactDuration(stats.totalMs), description: 'Total request time (no answer text was generated)' }
  }
  return { timing: `${stats.firstTokenMs}ms`, short: compactDuration(stats.firstTokenMs), description: firstToken }
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
