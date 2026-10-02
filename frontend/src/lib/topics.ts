// The chat topics (spec 2026-10-02 §5.1). Each keeps its own conversation,
// saved in localStorage under its API corpus (lib/conversation.ts).
import type { ApiCorpus } from './conversation.ts'
import type { IdkCorpus } from './idkReplies.ts'

export type Corpus = 'basel' | 'system' | 'portfolio'

export type Topic = { value: Corpus; label: string; short: string }

export const TOPICS: readonly Topic[] = [
  { value: 'basel', label: 'About Basel', short: 'Basel' },
  { value: 'system', label: 'About This System', short: 'System' },
  { value: 'portfolio', label: 'Portfolio', short: 'Portfolio' },
]

export const CORPORA: readonly Corpus[] = TOPICS.map((topic) => topic.value)

const API_CORPUS: Record<Corpus, ApiCorpus> = { basel: 'about_me', system: 'about_system', portfolio: 'portfolio' }

export function apiCorpus(corpus: Corpus): ApiCorpus {
  return API_CORPUS[corpus]
}

export function topicLabel(corpus: Corpus): string {
  return TOPICS.find((topic) => topic.value === corpus)!.label
}

/** Portfolio abstentions use the About Basel replies (they are about Basel's work). */
export function idkCorpus(corpus: Corpus): IdkCorpus {
  return corpus === 'system' ? 'system' : 'basel'
}
