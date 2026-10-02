// Build-time portfolio data (spec 2026-10-02 §7). Reads corpus/portfolio/*.md,
// parses each file's YAML front matter, validates every file with
// src/lib/portfolio.ts, drops `draft: true` files and exposes the sorted
// Project[] as the virtual module `virtual:portfolio`. Any invalid file stops
// the build (and the dev server's module) with every problem listed. No
// runtime fetch.
//
// It reads the files the way services/glassbox/portfolio.py (the CI check and
// the ingest scanner) does, so the site and the chatbot agree on every file:
// YAML 1.1 resolution like PyYAML's safe loader (dates, `1:20`, `0x1f`), but
// only the literal words `true` and `false` are booleans (`yes`, `on`, `True`
// are text) and a repeated field is an error; the front matter sits between
// two `---` lines; files in subfolders and symlinked files are errors; an
// image must be a real file with no symlink on its path. The personal-data
// check stays in CI (portfolio.py) only.
import { existsSync, lstatSync, readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import type { Plugin } from 'vite'
import { isMap, isScalar, parseDocument, visit, type ScalarTag } from 'yaml'
import { isDraft, sortProjects, validateProject, type Project } from '../src/lib/portfolio.ts'

export const VIRTUAL_ID = 'virtual:portfolio'
const RESOLVED_ID = `\0${VIRTUAL_ID}`

export class PortfolioFileError extends Error {}

/** The front matter (between a first `---` line and the next `---` line) and the body after it. */
export function splitFrontmatter(text: string): { frontmatter: string; body: string } {
  const lines = text.split(/(?<=\n)|(?<=\r)(?!\n)/)
  if (lines.length === 0 || lines[0].trim() !== '---') {
    throw new PortfolioFileError("missing front matter: the file must start with a '---' line")
  }
  const end = lines.findIndex((line, index) => index > 0 && line.trim() === '---')
  if (end === -1) throw new PortfolioFileError("front matter is not closed by a second '---' line")
  return { frontmatter: lines.slice(1, end).join(''), body: lines.slice(end + 1).join('') }
}

/** A YAML float. Never a whole number, even when integral (`2026.0`), as in PyYAML; printed as written. */
class YamlFloat {
  readonly source: string
  constructor(source: string) {
    this.source = source
  }
  toString() {
    return this.source
  }
}

// PyYAML's resolvers where the yaml package's YAML 1.1 schema differs: the
// backend reads only `true`/`false` as booleans, and PyYAML floats need a dot
// (`1e3` is text there).
const STRICT_BOOL: ScalarTag = {
  tag: 'tag:yaml.org,2002:bool',
  default: true,
  identify: (value) => typeof value === 'boolean',
  test: /^(?:true|false)$/,
  resolve: (source) => source === 'true',
}
const PYYAML_FLOAT: ScalarTag = {
  tag: 'tag:yaml.org,2002:float',
  default: true,
  identify: (value) => value instanceof YamlFloat,
  test: /^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$/,
  resolve: (source) => new YamlFloat(source),
}

/** Parses front matter like portfolio.py's strict loader; throws a PortfolioFileError with portfolio.py's wording. */
export function parseFrontmatter(frontmatter: string): unknown {
  const doc = parseDocument(frontmatter, {
    schema: 'yaml-1.1',
    // Duplicates are found below, so the message can name the field.
    uniqueKeys: false,
    customTags: (tags) => [
      ...tags.filter((tag) => typeof tag === 'string' || (tag.tag !== 'tag:yaml.org,2002:bool' && !(tag.tag === 'tag:yaml.org,2002:float' && tag.format !== 'TIME'))),
      STRICT_BOOL,
      PYYAML_FLOAT,
    ],
  })
  if (doc.errors.length > 0) throw new PortfolioFileError(`front matter is not valid YAML: ${doc.errors[0].message.split('\n')[0]}`)
  let duplicate: string | null = null
  visit(doc, {
    Map(_key, map) {
      const seen = new Set<unknown>()
      for (const pair of map.items) {
        const key = isScalar(pair.key) ? pair.key.value : pair.key
        if (seen.has(key)) {
          duplicate = String(key)
          return visit.BREAK
        }
        seen.add(key)
      }
      return undefined
    },
  })
  if (duplicate !== null) throw new PortfolioFileError(`front matter has the field '${duplicate}' more than once; keep one`)
  const data: unknown = doc.toJS({ maxAliasCount: 100 })
  if (!isMap(doc.contents)) throw new PortfolioFileError("front matter must be a list of 'field: value' lines")
  return data
}

function lstat(path: string) {
  try {
    return lstatSync(path)
  } catch {
    return null
  }
}

/** portfolio.py's missing_visual_files: the image, and every folder on the way to it, must not be a symlink. */
function visualProblem(publicPortfolioDir: string, src: string): string | null {
  let path = publicPortfolioDir
  const parts = src.split('/').filter((part) => part !== '' && part !== '.')
  for (const part of parts) {
    path = join(path, part)
    if (lstat(path)?.isSymbolicLink()) return `visual ${src} is a symlink; commit the image itself (symlinks are never read)`
  }
  if (!lstat(path)?.isFile()) return `visual ${src} is not in frontend/public/portfolio/`
  return null
}

/** Every Markdown file under the corpus folder, hidden paths left out (portfolio.py's portfolio_files). Symlinked folders are not followed. */
function markdownFiles(dir: string): string[] {
  const files: string[] = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) continue
    const path = join(dir, entry.name)
    if (entry.isDirectory()) files.push(...markdownFiles(path))
    else if (entry.name.endsWith('.md')) files.push(path)
  }
  return files
}

export function loadPortfolio(corpusDir: string, publicDir: string): Project[] {
  if (!existsSync(corpusDir)) {
    // The Docker frontend stage must copy it (see Dockerfile); an empty
    // portfolio must never ship by accident.
    throw new Error(`portfolio: ${corpusDir} not found (corpus/portfolio is missing from the build context)`)
  }
  const publicPortfolioDir = join(publicDir, 'portfolio')
  const projects: Project[] = []
  const errors: string[] = []
  for (const path of markdownFiles(corpusDir).sort()) {
    const name = relative(corpusDir, path).split('\\').join('/')
    const fail = (message: string) => { errors.push(`${name}: ${message}`) }
    if (name.includes('/')) {
      fail('project files go directly in corpus/portfolio/, not in a subfolder')
      continue
    }
    if (lstat(path)?.isSymbolicLink()) {
      fail('symlinks are never read; commit the file itself')
      continue
    }
    let text: string
    try {
      text = new TextDecoder('utf-8', { fatal: true }).decode(readFileSync(path))
    } catch (error) {
      fail(`cannot read UTF-8 content: ${(error as Error).message}`)
      continue
    }
    let data: unknown
    let body: string
    try {
      const parts = splitFrontmatter(text)
      data = parseFrontmatter(parts.frontmatter)
      body = parts.body
    } catch (error) {
      if (!(error instanceof PortfolioFileError)) throw error
      fail(error.message)
      continue
    }
    const draft = isDraft(data)
    // Drafts are validated too (CI does the same), but their images are not checked.
    const result = validateProject(name.slice(0, -'.md'.length), data, body, draft ? undefined : (src) => visualProblem(publicPortfolioDir, src))
    if (result.project) {
      if (!draft) projects.push(result.project)
    } else {
      errors.push(...result.errors)
    }
  }
  if (errors.length > 0) throw new Error(`Invalid portfolio files (corpus/portfolio; check with python -m services.glassbox.portfolio):\n- ${errors.join('\n- ')}`)
  return sortProjects(projects)
}

export function portfolioPlugin({ corpusDir, publicDir }: { corpusDir: string; publicDir: string }): Plugin {
  const publicPortfolioDir = join(publicDir, 'portfolio')
  return {
    name: 'glassbox-portfolio',
    resolveId(id) {
      return id === VIRTUAL_ID ? RESOLVED_ID : null
    },
    load(id) {
      if (id !== RESOLVED_ID) return null
      return `export default ${JSON.stringify(loadPortfolio(corpusDir, publicDir))}`
    },
    configureServer(server) {
      // Dev: editing, adding or removing a project file or an image reloads
      // the page with fresh data (or the error overlay).
      server.watcher.add([corpusDir, publicPortfolioDir])
      const reload = (file: string) => {
        if (!file.startsWith(corpusDir) && !file.startsWith(publicPortfolioDir)) return
        const graph = server.environments.client.moduleGraph
        const module = graph.getModuleById(RESOLVED_ID)
        if (module) graph.invalidateModule(module)
        server.ws.send({ type: 'full-reload' })
      }
      server.watcher.on('add', reload)
      server.watcher.on('change', reload)
      server.watcher.on('unlink', reload)
    },
  }
}
