// The chat topics (spec 2026-10-02 §5.1; two topics since 2026-10-09). Each
// keeps its own conversation, saved in localStorage under its API corpus
// (lib/conversation.ts). About Basel answers about Basel, the projects and
// freelance work (its retrieval also searches the portfolio files); its panel
// is the project grid. About This System's panel is the live diagram.
import type { ApiCorpus } from './conversation.ts'
import type { OtherView } from './diagramNav.ts'
import type { IdkCorpus } from './idkReplies.ts'

export type Corpus = 'basel' | 'system'

export type Topic = { value: Corpus; label: string; short: string }

export const TOPICS: readonly Topic[] = [
  { value: 'basel', label: 'About Basel', short: 'Basel' },
  { value: 'system', label: 'About This System', short: 'System' },
]

export const CORPORA: readonly Corpus[] = TOPICS.map((topic) => topic.value)

const API_CORPUS: Record<Corpus, ApiCorpus> = { basel: 'about_me', system: 'about_system' }

export function apiCorpus(corpus: Corpus): ApiCorpus {
  return API_CORPUS[corpus]
}

export function topicLabel(corpus: Corpus): string {
  return TOPICS.find((topic) => topic.value === corpus)!.label
}

export function idkCorpus(corpus: Corpus): IdkCorpus {
  return corpus
}

/**
 * The topic's own panel (owner, 2026-10-09, phone option A): About Basel's is
 * the project grid (the `portfolio` view), About This System's the diagram.
 * With no published project About Basel falls back to the diagram, as before.
 */
export function panelView(corpus: Corpus, hasProjects: boolean): OtherView {
  return corpus === 'basel' && hasProjects ? 'portfolio' : 'diagram'
}

/**
 * The topic a panel belongs to: opening a view by any route also picks its
 * topic. With no published project the diagram is both topics' panel, so
 * opening it keeps the current topic (#210 review).
 */
export function topicForView(view: OtherView, current: Corpus, hasProjects: boolean): Corpus {
  if (view === 'portfolio') return 'basel'
  return hasProjects ? 'system' : current
}

/**
 * The topic on load: the address bar's (it always mirrors the shown topic),
 * else the topic of the phone view a reload restored, else About Basel. A
 * phone panel that disagrees is then switched to the topic's own (App.tsx).
 */
export function initialTopic(linked: Corpus | null, view: OtherView | 'chat', hasProjects: boolean): Corpus {
  if (linked !== null) return linked
  return view === 'chat' ? 'basel' : topicForView(view, 'basel', hasProjects)
}
