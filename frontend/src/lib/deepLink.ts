// Shareable links into the site: `?project=<slug>` opens the Portfolio with
// that project's details sheet, and `?topic=basel|system|portfolio` picks the
// topic. Pure, so the parsing is unit tested. A deep link never asks the
// chatbot by itself (a shared link must not spend an answer per visit); the
// sheet already shows the write-up. Unknown, hidden or draft values are ignored.

import type { Corpus } from './topics.ts'

export type DeepLink = { corpus: Corpus | null; project: string | null }

const TOPICS: readonly Corpus[] = ['basel', 'system', 'portfolio']

/** `projectSlugs` are the published projects; Portfolio counts only when there is one. */
export function parseDeepLink(search: string, projectSlugs: readonly string[]): DeepLink {
  const params = new URLSearchParams(search)
  const slug = params.get('project')
  if (slug !== null && projectSlugs.includes(slug)) return { corpus: 'portfolio', project: slug }
  const topic = params.get('topic') as Corpus | null
  if (topic !== null && TOPICS.includes(topic) && (topic !== 'portfolio' || projectSlugs.length > 0)) {
    return { corpus: topic, project: null }
  }
  return { corpus: null, project: null }
}
