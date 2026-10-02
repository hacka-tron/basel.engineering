// Portfolio panel copy and small view helpers (spec 2026-10-02 §5.4).

export const PORTFOLIO_DETAILS_HINT = 'Select a project for details'
/** Phone status text left of the view toggle in Portfolio view. */
export const PORTFOLIO_STATUS_HINT = 'Select a project'
export const PORTFOLIO_EMPTY_TEXT = 'Projects are on their way. Ask the chat in the meantime.'

/** Selecting a project asks this on the Portfolio topic, without history (like component inspect). */
export function questionForProject(title: string): string {
  return `Tell me about ${title}`
}

export function portfolioHeading(count: number): string {
  if (count === 0) return 'Portfolio'
  return `Portfolio · ${count} ${count === 1 ? 'project' : 'projects'}`
}

export function stackPreview(stack: readonly string[], limit = 3): { shown: string[]; rest: number } {
  const shown = stack.slice(0, limit)
  return { shown, rest: stack.length - shown.length }
}

/** A generated thumbnail for a project with no visuals: two hues from its slug. */
export function placeholderColors(slug: string): { from: string; to: string } {
  let hash = 7
  for (const char of slug) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  const hue = hash % 360
  return { from: `hsl(${hue} 45% 34%)`, to: `hsl(${(hue + 40) % 360} 40% 16%)` }
}

/** First letters of the first two words (spaces or hyphens), or the first letter of a one-word title. */
export function initials(title: string): string {
  const words = title.split(/[\s-]+/).filter(Boolean)
  if (words.length === 0) return ''
  if (words.length === 1) return words[0][0].toUpperCase()
  return (words[0][0] + words[1][0]).toUpperCase()
}
