import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { loadPortfolio, parseFrontmatter, splitFrontmatter } from './portfolio.ts'

function fixture(files: Record<string, string | Buffer>, images: string[] = []) {
  const root = mkdtempSync(join(tmpdir(), 'portfolio-'))
  const corpus = join(root, 'corpus')
  const publicDir = join(root, 'public')
  mkdirSync(corpus)
  for (const [name, text] of Object.entries(files)) {
    mkdirSync(join(corpus, name, '..'), { recursive: true })
    writeFileSync(join(corpus, name), text)
  }
  for (const image of images) {
    mkdirSync(join(publicDir, 'portfolio', image, '..'), { recursive: true })
    writeFileSync(join(publicDir, 'portfolio', image), 'png')
  }
  return { root, corpus, publicDir, cleanup: () => rmSync(root, { recursive: true, force: true }) }
}

const file = (front: string, body = 'About it.') => `---\n${front}\n---\n${body}\n`
const base = 'one_liner: Does a thing.\nkind: personal\nyear: 2026\nstack: [TypeScript]'

function loadErrors(f: ReturnType<typeof fixture>): string {
  try {
    loadPortfolio(f.corpus, f.publicDir)
  } catch (error) {
    return (error as Error).message
  }
  return ''
}

test('front matter sits between the first two --- lines (trailing spaces allowed, as in portfolio.py)', () => {
  assert.deepEqual(splitFrontmatter('---\na: 1\n---\nBody\n'), { frontmatter: 'a: 1\n', body: 'Body\n' })
  assert.deepEqual(splitFrontmatter('--- \r\na: 1\r\n---\r\n'), { frontmatter: 'a: 1\r\n', body: '' })
  assert.deepEqual(splitFrontmatter('\uFEFF---\na: 1\n---\n'), { frontmatter: 'a: 1\n', body: '' })
  assert.throws(() => splitFrontmatter('No frontmatter'), /missing front matter/)
  assert.throws(() => splitFrontmatter('---\na: 1\n'), /not closed/)
})

test('YAML is read like the backend: only true/false are booleans, PyYAML 1.1 numbers and dates', () => {
  const data = parseFrontmatter('a: true\nb: false\nc: yes\nd: True\ne: on\nf: 1:20\ng: 2026-01-01\nh: 1e3\ni: 0x1f\nj: ~') as Record<string, unknown>
  assert.equal(data.a, true)
  assert.equal(data.b, false)
  assert.equal(data.c, 'yes')
  assert.equal(data.d, 'True')
  assert.equal(data.e, 'on')
  assert.equal(data.f, 80)
  assert.ok(data.g instanceof Date)
  assert.equal(data.h, '1e3')
  assert.equal(data.i, 31)
  assert.equal(data.j, null)
  // A float is never a whole number, even 2026.0.
  assert.equal(typeof (parseFrontmatter('year: 2026.0') as Record<string, unknown>).year, 'object')
})

test('a repeated field is an error, at any depth', () => {
  assert.throws(() => parseFrontmatter('title: A\ntitle: B'), /the field 'title' more than once/)
  assert.throws(() => parseFrontmatter('links:\n  live: https://a.com\n  live: https://b.com'), /the field 'live' more than once/)
  assert.throws(() => parseFrontmatter('title: [unclosed'), /not valid YAML/)
  assert.throws(() => parseFrontmatter('- a\n- b'), /must be a list of 'field: value' lines/)
  assert.throws(() => parseFrontmatter(''), /must be a list of 'field: value' lines/)
})

test('loads valid files sorted by order, skipping drafts, dotfiles and non-Markdown', () => {
  const f = fixture({
    'beta.md': file(`title: Beta\norder: 2\n${base}`),
    'alpha.md': file(`title: Alpha\norder: 1\n${base}\nvisuals:\n  - src: alpha/a.png\n    alt: Screen\n    aspect: 4/3`),
    '_example.md': file(`draft: true\ntitle: Example\n${base}\nvisuals:\n  - src: _example/missing.png\n    alt: x\n    aspect: 4/3`),
    '.hidden.md': file('title: [broken'),
    'notes.txt': 'x',
  }, ['alpha/a.png'])
  try {
    const projects = loadPortfolio(f.corpus, f.publicDir)
    assert.deepEqual(projects.map((p) => p.slug), ['alpha', 'beta'])
    assert.equal(projects[0].visuals[0].src, '/portfolio/alpha/a.png')
  } finally {
    f.cleanup()
  }
})

test('the repository corpus loads and leaves out the draft example', () => {
  const corpus = new URL('../../corpus/portfolio', import.meta.url).pathname
  const projects = loadPortfolio(corpus, new URL('../public', import.meta.url).pathname)
  assert.ok(projects.some((p) => p.slug === 'goalbuddy'))
  assert.ok(!projects.some((p) => p.slug === '_example'))
  assert.match(readFileSync(join(corpus, '_example.md'), 'utf8'), /draft: true/)
})

test('drafts are validated too, like the CI check', () => {
  const f = fixture({ '_example.md': file('draft: true\ntitle: Example') })
  try {
    assert.match(loadErrors(f), /_example\.md: missing required field 'one_liner'/)
  } finally {
    f.cleanup()
  }
})

test('draft: yes or True is an error, not a draft (the backend would disagree otherwise)', () => {
  for (const draft of ['yes', 'True', 'on']) {
    const f = fixture({ 'a.md': file(`title: A\n${base}\ndraft: ${draft}`) })
    try {
      assert.match(loadErrors(f), /a\.md: draft must be true or false/, draft)
    } finally {
      f.cleanup()
    }
  }
})

test('every invalid file is listed in one error, and the build stops', () => {
  const f = fixture({
    'a.md': file(`title: A\n${base}\nvisuals:\n  - src: a/missing.png\n    alt: x\n    aspect: 4/3`),
    'b.md': file('title: B'),
    'c.md': 'no frontmatter at all',
    'd.md': file('title: [unclosed'),
    'e.md': file(`title: E\ntitle: E2\n${base}`),
    'sub/f.md': file(`title: F\n${base}`),
    'g.md': Buffer.from([0x2d, 0x2d, 0x2d, 0x0a, 0xff, 0xfe, 0x0a]),
  })
  try {
    const message = loadErrors(f)
    assert.match(message, /a\.md: visual a\/missing\.png is not in frontend\/public\/portfolio\//)
    assert.match(message, /b\.md: missing required field 'one_liner'/)
    assert.match(message, /c\.md: missing front matter/)
    assert.match(message, /d\.md: front matter is not valid YAML/)
    assert.match(message, /e\.md: front matter has the field 'title' more than once/)
    assert.match(message, /sub\/f\.md: project files go directly in corpus\/portfolio\//)
    assert.match(message, /g\.md: cannot read UTF-8 content/)
  } finally {
    f.cleanup()
  }
})

test('symlinked project files and images are errors', () => {
  const f = fixture({ 'real.md': file(`title: Real\n${base}\nvisuals:\n  - src: real/a.png\n    alt: x\n    aspect: 4/3`) }, ['elsewhere/a.png'])
  try {
    symlinkSync(join(f.corpus, 'real.md'), join(f.corpus, 'link.md'))
    symlinkSync(join(f.publicDir, 'portfolio', 'elsewhere'), join(f.publicDir, 'portfolio', 'real'))
    const message = loadErrors(f)
    assert.match(message, /link\.md: symlinks are never read/)
    assert.match(message, /real\.md: visual real\/a\.png is a symlink/)
  } finally {
    f.cleanup()
  }
})

test('loadPortfolio throws when the corpus directory is missing (a build that cannot see corpus/portfolio must not ship an empty portfolio)', () => {
  assert.throws(() => loadPortfolio(join(tmpdir(), 'no-such-portfolio-dir'), tmpdir()), /corpus\/portfolio is missing/)
})
