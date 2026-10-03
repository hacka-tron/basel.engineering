import type { Corpus } from './topics.ts'

/**
 * The "Searched for: ..." line (the rewritten follow-up query) shows only in
 * About This System. Owner decision 2026-10-03: About Basel and Portfolio
 * answers don't show it.
 */
export function searchedForText(corpus: Corpus, rewrittenQuery: string | undefined): string | null {
  if (corpus !== 'system' || !rewrittenQuery) return null
  return rewrittenQuery
}
