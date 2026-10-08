// Portfolio projects (spec 2026-10-02 §6.1, §7): the frontmatter schema of
// corpus/portfolio/<slug>.md, its validation, and the small Markdown subset
// the details sheet renders (paragraphs, lists and links; raw HTML is only
// ever text). Pure, with no imports: the build plugin
// (vite-plugins/portfolio.ts), the app and the unit tests all load it.
//
// The rules mirror services/glassbox/portfolio.py (the CI check and the
// ingest scanner) so the site and the chatbot never disagree about a file:
// the same required and optional fields, unknown fields are errors, `kind`
// personal | freelance, `year` a whole number 1990 to 2100, `order` a whole
// number, `stack` a non-empty list of text, `links` live/code as https://
// URLs, `visuals` src/alt/aspect (+ caption) with src under the file's slug,
// `cover` (the card picture) a path under the file's slug,
// `draft` exactly true or false, and file names lowercase slugs after at most
// one leading underscore. YAML-level rules (only the literal words true/false
// are booleans, a repeated field is an error, floats are never whole numbers)
// are applied by the loader's parser. Stricter here, on purpose: a visual's
// src must be an image file with no hidden path segment, and body links must
// be https:// (the sheet renders them as links).

export const KINDS = ['personal', 'freelance'] as const
export type Kind = (typeof KINDS)[number]

export const ASPECTS = { '16/10': 16 / 10, '4/3': 4 / 3, '9/19.5': 9 / 19.5 } as const
export type Aspect = keyof typeof ASPECTS

/** Nominal intrinsic height for the img width/height attributes; only the ratio matters (no layout shift while loading). */
export const VISUAL_HEIGHT = 600
/** frontend/public/portfolio/ is served at /portfolio/ (same origin; the CSP allows only img-src 'self'). */
export const VISUALS_URL_BASE = '/portfolio/'

export type Inline = { kind: 'text'; text: string } | { kind: 'link'; text: string; href: string }
export type Block = { kind: 'p'; inlines: Inline[] } | { kind: 'ul' | 'ol'; items: Inline[][] }
export type Visual = { src: string; alt: string; caption?: string; aspect: Aspect; width: number; height: number }
export type Project = {
  slug: string
  title: string
  oneLiner: string
  kind: Kind
  year: number
  order: number | null
  stack: string[]
  links: { live?: string; code?: string }
  /** The card picture when set; otherwise the card shows the first visual. Never in the visuals gallery. */
  cover: string | null
  visuals: Visual[]
  body: Block[]
}

const SLUG = /^_?[a-z0-9][a-z0-9-]*$/
const REQUIRED_FIELDS = ['title', 'one_liner', 'kind', 'year', 'stack'] as const
const KNOWN_FIELDS = new Set<string>([...REQUIRED_FIELDS, 'order', 'links', 'cover', 'visuals', 'draft'])
const LINK_KEYS = ['live', 'code'] as const
const VISUAL_FIELDS = new Set(['src', 'alt', 'caption', 'aspect'])
const MIN_YEAR = 1990
const MAX_YEAR = 2100
// Same as portfolio.py's _HTTPS_URL (full match): https://, a host, no whitespace.
const HTTPS_URL = /^https:\/\/[^\s/]+\S*$/
const IMAGE = /\.(png|jpe?g|webp|avif|gif|svg)$/i

/** A YAML mapping (a plain object; arrays, dates and other parsed values are not). */
function isMapping(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function isText(value: unknown): value is string {
  return typeof value === 'string' && value.trim() !== ''
}

function isWholeNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value)
}

function isHttps(value: unknown): value is string {
  return typeof value === 'string' && HTTPS_URL.test(value)
}

/** Python truthiness, for portfolio.py's `data.get("links") or {}`: an empty or zero value counts as left out. */
function isFalsy(value: unknown): boolean {
  if (value === null || value === undefined || value === false || value === 0 || value === '') return true
  if (Array.isArray(value)) return value.length === 0
  if (isMapping(value)) return Object.keys(value).length === 0
  return false
}

/** A value for an error message, like Python's repr: text quoted, everything else as written. */
function shown(value: unknown): string {
  if (typeof value === 'string') return `'${value}'`
  if (value === null || value === undefined) return 'None'
  if (Array.isArray(value) || isMapping(value)) return JSON.stringify(value)
  return String(value)
}

/** True only for a mapping whose `draft` is the boolean true (the loader's parser reads only `true`/`false` as booleans). */
export function isDraft(data: unknown): boolean {
  return isMapping(data) && data.draft === true
}

const LINK = /\[([^\]\n]+)\]\(([^)\s]+)\)/g
const UL = /^\s*[-*]\s+/
const OL = /^\s*\d+[.)]\s+/
const HEADING = /^#{1,6}\s+/

function parseInlines(text: string, onError: (message: string) => void): Inline[] {
  const inlines: Inline[] = []
  let last = 0
  for (const match of text.matchAll(LINK)) {
    const [whole, label, href] = match
    const at = match.index
    if (at > last) inlines.push({ kind: 'text', text: text.slice(last, at) })
    if (isHttps(href)) {
      inlines.push({ kind: 'link', text: label, href })
    } else {
      onError(`body link "${href}" must be an https:// URL`)
      inlines.push({ kind: 'text', text: whole })
    }
    last = at + whole.length
  }
  if (last < text.length) inlines.push({ kind: 'text', text: text.slice(last) })
  // Merge neighbouring text runs (a rejected link sits between two).
  return inlines.reduce<Inline[]>((merged, inline) => {
    const previous = merged.at(-1)
    if (previous?.kind === 'text' && inline.kind === 'text') previous.text += inline.text
    else merged.push({ ...inline })
    return merged
  }, [])
}

/**
 * The "About the project" body: blank-line-separated blocks. A block whose
 * first line starts with "- " or "* " is a bulleted list, "1. " a numbered
 * one (other lines continue the item); anything else is a paragraph whose
 * lines are joined, with a leading "#" heading marker dropped. Links are
 * [text](https://...). Everything else, raw HTML included, is plain text.
 */
export function parseBody(markdown: string, onError: (message: string) => void = () => {}): Block[] {
  const blocks: Block[] = []
  for (const chunk of markdown.replace(/\r\n?/g, '\n').split(/\n\s*\n/)) {
    const lines = chunk.split('\n').filter((line) => line.trim() !== '')
    if (lines.length === 0) continue
    const listKind = UL.test(lines[0]) ? 'ul' : OL.test(lines[0]) ? 'ol' : null
    if (listKind) {
      const marker = listKind === 'ul' ? UL : OL
      const items: string[] = []
      for (const line of lines) {
        if (marker.test(line)) items.push(line.replace(marker, '').trim())
        else items[items.length - 1] += ` ${line.trim()}`
      }
      blocks.push({ kind: listKind, items: items.map((item) => parseInlines(item, onError)) })
    } else {
      blocks.push({ kind: 'p', inlines: parseInlines(lines.map((line) => line.trim().replace(HEADING, '')).join(' '), onError) })
    }
  }
  return blocks
}

/**
 * Problems with a visual's `src`, which is relative to frontend/public/portfolio/
 * and must start with the file's slug (portfolio.py's rule), plus this
 * module's own: an image file, no hidden segment.
 */
function srcProblem(src: string, slug: string, label: string): string | null {
  // PurePosixPath(src).parts: empty and "." segments drop out.
  const parts = src.split('/').filter((part) => part !== '' && part !== '.')
  const outside = src.includes('\\') || src.startsWith('/') || parts.includes('..')
  if (outside || parts.length < 2 || parts[0] !== slug) {
    return `${label} must be a path like ${slug}/picture.png under frontend/public/portfolio/ (got ${shown(src)})`
  }
  if (parts.some((part) => part.startsWith('.')) || !IMAGE.test(src)) {
    return `${label} must be an image file (.png, .jpg, .webp, .avif, .gif or .svg) with no hidden folder or file (got ${shown(src)})`
  }
  return null
}

/** The served URL of an image under frontend/public/portfolio/. */
function publicUrl(src: string): string {
  return VISUALS_URL_BASE + src.split('/').filter((part) => part !== '' && part !== '.').map(encodeURIComponent).join('/')
}

export type ValidationResult = { project: Project; errors: [] } | { project: null; errors: string[] }

/**
 * Validates one file's frontmatter (`data`) and body. `checkVisual` returns a
 * problem with frontend/public/portfolio/<src> (missing, a symlink) or null;
 * omitted (drafts), the images are not checked. Every error names the file.
 */
export function validateProject(slug: string, data: unknown, body: string, checkVisual?: (src: string) => string | null): ValidationResult {
  const errors: string[] = []
  const fail = (message: string) => { errors.push(`${slug}.md: ${message}`) }
  if (!SLUG.test(slug)) fail(`file name ${slug}.md must be lowercase letters, digits and hyphens (like jobpilot.md)`)
  if (!isMapping(data)) {
    fail("front matter must be a list of 'field: value' lines")
    return { project: null, errors }
  }
  const unknown = Object.keys(data).filter((key) => !KNOWN_FIELDS.has(key)).sort()
  if (unknown.length > 0) fail(`unknown fields: ${unknown.join(', ')}`)
  for (const name of REQUIRED_FIELDS) {
    if (data[name] === undefined || data[name] === null) fail(`missing required field '${name}'`)
  }
  const { title, one_liner: oneLiner, kind, year, stack } = data
  for (const [name, value] of [['title', title], ['one_liner', oneLiner]] as const) {
    if (value != null && !isText(value)) fail(`${name} must be text`)
  }
  if (kind != null && !(KINDS as readonly unknown[]).includes(kind)) fail(`kind must be one of ${KINDS.join(', ')} (got ${shown(kind)})`)
  if (year != null && !(isWholeNumber(year) && year >= MIN_YEAR && year <= MAX_YEAR)) fail(`year must be a whole number like 2026 (got ${shown(year)})`)
  if (stack != null && !(Array.isArray(stack) && stack.length > 0 && stack.every(isText))) fail('stack must be a list like [TypeScript, React]')
  const order = data.order ?? null
  if (order !== null && !isWholeNumber(order)) fail(`order must be a whole number (got ${shown(order)})`)

  const links: Project['links'] = {}
  const rawLinks = isFalsy(data.links) ? {} : data.links
  if (!isMapping(rawLinks)) {
    fail('links must have live and/or code entries')
  } else {
    for (const [key, url] of Object.entries(rawLinks)) {
      if (!(LINK_KEYS as readonly string[]).includes(key)) fail(`unknown link '${key}' (use ${LINK_KEYS.join(', ')})`)
      else if (!isHttps(url)) fail(`links.${key} must be an https:// URL (got ${shown(url)})`)
      else links[key as 'live' | 'code'] = url
    }
  }

  let cover: string | null = null
  if (!isFalsy(data.cover)) {
    if (!isText(data.cover)) {
      fail(`cover must be a path like ${slug}/cover.png (got ${shown(data.cover)})`)
    } else {
      const problem = srcProblem(data.cover, slug, 'cover') ?? checkVisual?.(data.cover) ?? null
      if (problem) fail(problem)
      else cover = publicUrl(data.cover)
    }
  }

  const visuals: Visual[] = []
  const rawVisuals = isFalsy(data.visuals) ? [] : data.visuals
  if (!Array.isArray(rawVisuals)) {
    fail('visuals must be a list')
  } else {
    rawVisuals.forEach((item: unknown, index: number) => {
      const where = `visuals[${index}]`
      if (!isMapping(item)) {
        fail(`${where} must have src, alt and aspect fields`)
        return
      }
      const before = errors.length
      const unknownVisual = Object.keys(item).filter((key) => !VISUAL_FIELDS.has(key)).sort()
      if (unknownVisual.length > 0) fail(`${where} has unknown fields: ${unknownVisual.join(', ')}`)
      const { src, alt, aspect, caption } = item
      if (!isText(src)) {
        fail(`${where}.src is missing`)
      } else {
        const problem = srcProblem(src, slug, `${where}.src`) ?? checkVisual?.(src) ?? null
        if (problem) fail(problem)
      }
      if (!isText(alt)) fail(`${where}.alt is missing (describe the image for screen readers)`)
      if (typeof aspect !== 'string' || !Object.hasOwn(ASPECTS, aspect)) {
        // aspect: 16:10 unquoted is read by YAML as the number 970.
        const hint = aspect != null && typeof aspect !== 'string' ? '; put it in quotes, like aspect: "16/10"' : ''
        fail(`${where}.aspect must be one of ${Object.keys(ASPECTS).join(', ')} (got ${shown(aspect)})${hint}`)
      }
      if (caption != null && typeof caption !== 'string') fail(`${where}.caption must be text`)
      if (errors.length > before) return
      const fit = aspect as Aspect
      visuals.push({
        src: publicUrl(src as string),
        alt: (alt as string).trim(),
        ...(isText(caption) ? { caption: caption.trim() } : {}),
        aspect: fit,
        width: Math.round(VISUAL_HEIGHT * ASPECTS[fit]),
        height: VISUAL_HEIGHT,
      })
    })
  }
  if (data.draft !== undefined && typeof data.draft !== 'boolean') fail(`draft must be true or false (got ${shown(data.draft)})`)

  const blocks = parseBody(body, fail)
  if (errors.length > 0) return { project: null, errors }
  return {
    project: {
      slug,
      title: (title as string).trim(),
      oneLiner: (oneLiner as string).trim(),
      kind: kind as Kind,
      year: year as number,
      order: order as number | null,
      stack: (stack as string[]).map((name) => name.trim()),
      links,
      cover,
      visuals,
      body: blocks,
    },
    errors: [],
  }
}

/** Grid order: ascending `order`, files without one last, then by title. */
export function sortProjects(projects: readonly Project[]): Project[] {
  return [...projects].sort((a, b) => (a.order ?? Infinity) - (b.order ?? Infinity) || a.title.localeCompare(b.title))
}
