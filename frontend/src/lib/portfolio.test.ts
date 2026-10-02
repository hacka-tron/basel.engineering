import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isDraft, parseBody, sortProjects, validateProject, type Project } from './portfolio.ts'

const valid = {
  title: 'JobPilot',
  one_liner: 'Job-search copilot that tailors applications.',
  kind: 'personal',
  year: 2026,
  order: 1,
  stack: ['TypeScript', 'React', 'FastAPI', 'Postgres'],
  links: { live: 'https://example.com', code: 'https://github.com/example/jobpilot' },
  visuals: [
    { src: 'jobpilot/board.png', alt: 'Pipeline board', caption: 'From saved to offer.', aspect: '16/10' },
    { src: 'jobpilot/phone.png', alt: 'Phone view', aspect: '9/19.5' },
  ],
}
const ok = () => null

function errorsFor(data: unknown, body = '', checkVisual: (src: string) => string | null = ok, slug = 'jobpilot') {
  return validateProject(slug, data, body, checkVisual).errors
}
const has = (errors: string[], text: string) => errors.some((error) => error.includes(text))

test('a valid file becomes a Project with served image URLs and intrinsic sizes', () => {
  const result = validateProject('jobpilot', valid, 'The problem.\n\nWhat was built.', ok)
  assert.deepEqual(result.errors, [])
  const project = result.project!
  assert.equal(project.slug, 'jobpilot')
  assert.equal(project.oneLiner, valid.one_liner)
  assert.equal(project.order, 1)
  assert.deepEqual(project.links, valid.links)
  assert.deepEqual(project.visuals[0], { src: '/portfolio/jobpilot/board.png', alt: 'Pipeline board', caption: 'From saved to offer.', aspect: '16/10', width: 960, height: 600 })
  assert.equal(project.visuals[1].width, 277) // 600 * 9 / 19.5
  assert.equal(project.body.length, 2)
})

test('optional fields default: no order, links, visuals or body; empty or null ones count as left out (as in portfolio.py)', () => {
  const { order: _order, links: _links, visuals: _visuals, ...minimal } = valid
  const project = validateProject('jobpilot', minimal, '', ok).project!
  assert.equal(project.order, null)
  assert.deepEqual(project.links, {})
  assert.deepEqual(project.visuals, [])
  assert.deepEqual(project.body, [])
  assert.deepEqual(errorsFor({ ...minimal, order: null, links: null, visuals: null }), [])
  assert.deepEqual(errorsFor({ ...minimal, links: {}, visuals: [] }), [])
})

test('every required field is reported when missing or null', () => {
  for (const field of ['title', 'one_liner', 'kind', 'year', 'stack'] as const) {
    const { [field]: _dropped, ...rest } = valid
    assert.ok(has(errorsFor(rest), `missing required field '${field}'`), field)
    assert.ok(has(errorsFor({ ...valid, [field]: null }), `missing required field '${field}'`), `${field}: null`)
  }
})

test('field types follow portfolio.py: text, whole numbers, a non-empty stack', () => {
  assert.ok(has(errorsFor({ ...valid, title: 2048 }), 'title must be text'))
  assert.ok(has(errorsFor({ ...valid, one_liner: '  ' }), 'one_liner must be text'))
  assert.ok(has(errorsFor({ ...valid, year: 1989 }), 'year must be a whole number'))
  assert.ok(has(errorsFor({ ...valid, year: '2026' }), 'year must be a whole number'))
  assert.ok(has(errorsFor({ ...valid, year: true }), 'year must be a whole number'))
  assert.ok(has(errorsFor({ ...valid, order: 1.5 }), 'order must be a whole number'))
  assert.ok(has(errorsFor({ ...valid, stack: [] }), 'stack must be a list'))
  assert.ok(has(errorsFor({ ...valid, stack: 'React' }), 'stack must be a list'))
  assert.ok(has(errorsFor({ ...valid, stack: ['React', 3] }), 'stack must be a list'))
})

test('draft must be exactly true or false; only the boolean true is a draft', () => {
  assert.deepEqual(errorsFor({ ...valid, draft: false }), [])
  assert.deepEqual(errorsFor({ ...valid, draft: true }), [])
  for (const draft of ['yes', 'True', 'on', null, 1]) {
    assert.ok(has(errorsFor({ ...valid, draft }), 'draft must be true or false'), String(draft))
  }
  assert.equal(isDraft({ draft: true }), true)
  assert.equal(isDraft({ draft: 'yes' }), false)
  assert.equal(isDraft({ draft: 'True' }), false)
  assert.equal(isDraft(null), false)
})

test('unknown kind, aspect or field, a missing alt, a missing image and a non-https link are rejected', () => {
  assert.ok(has(errorsFor({ ...valid, kind: 'agency' }), 'kind must be one of personal, freelance'))
  assert.ok(has(errorsFor({ ...valid, visuals: [{ ...valid.visuals[0], aspect: '1/1' }] }), 'visuals[0].aspect must be one of'))
  assert.ok(has(errorsFor({ ...valid, visuals: [{ ...valid.visuals[0], aspect: 970 }] }), 'put it in quotes'))
  assert.ok(has(errorsFor({ ...valid, 'one-liner': 'typo' }), 'unknown fields: one-liner'))
  assert.ok(has(errorsFor({ ...valid, visuals: [{ ...valid.visuals[0], size: 'big' }] }), 'visuals[0] has unknown fields: size'))
  assert.ok(has(errorsFor({ ...valid, visuals: [{ src: 'jobpilot/a.png', aspect: '4/3' }] }), 'visuals[0].alt is missing'))
  assert.ok(has(errorsFor({ ...valid, visuals: [{ ...valid.visuals[0], caption: 3 }] }), 'caption must be text'))
  assert.ok(has(errorsFor({ ...valid, visuals: ['jobpilot/a.png'] }), 'visuals[0] must have src, alt and aspect'))
  assert.ok(has(errorsFor({ ...valid, visuals: 'jobpilot/a.png' }), 'visuals must be a list'))
  assert.ok(has(errorsFor(valid, '', () => 'visual jobpilot/board.png is not in frontend/public/portfolio/'), 'is not in frontend/public/portfolio/'))
  assert.ok(has(errorsFor({ ...valid, links: { live: 'http://example.com' } }), 'links.live must be an https:// URL'))
  assert.ok(has(errorsFor({ ...valid, links: { live: 'HTTPS://example.com' } }), 'links.live must be an https:// URL'))
  assert.ok(has(errorsFor({ ...valid, links: { live: 'https://exa mple.com' } }), 'links.live must be an https:// URL'))
  assert.ok(has(errorsFor({ ...valid, links: { demo: 'https://example.com' } }), "unknown link 'demo'"))
  assert.ok(has(errorsFor({ ...valid, links: ['https://example.com'] }), 'links must have live and/or code'))
})

test('drafts skip only the image check', () => {
  assert.deepEqual(validateProject('jobpilot', { ...valid, draft: true }, '').errors, [])
})

test('image paths must start with the slug and stay inside frontend/public/portfolio', () => {
  for (const src of ['../secret.png', '/etc/x.png', 'https://cdn.example.com/a.png', 'jobpilot/../../x.png', 'other/a.png', 'jobpilot', 'jobpilot\\a.png', 'jobpilot/.hidden.png', 'jobpilot/.git/a.png', 'jobpilot/notes.txt']) {
    assert.ok(has(errorsFor({ ...valid, visuals: [{ src, alt: 'x', aspect: '4/3' }] }), '.src must be'), src)
  }
  const project = validateProject('jobpilot', { ...valid, visuals: [{ src: 'jobpilot/./My Screen.png', alt: 'x', aspect: '4/3' }] }, '', ok).project!
  assert.equal(project.visuals[0].src, '/portfolio/jobpilot/My%20Screen.png')
})

test('file names must be lowercase slugs (one leading underscore allowed), and errors name the file', () => {
  const errors = errorsFor(valid, '', ok, 'Job Pilot')
  assert.ok(errors[0].startsWith('Job Pilot.md: '))
  assert.ok(has(errors, 'must be lowercase letters, digits and hyphens'))
  assert.equal(has(errorsFor({ ...valid, visuals: [] }, '', ok, '_jobpilot'), 'file name'), false)
})

test('the body keeps paragraphs, lists and https links; HTML stays text; headings become text', () => {
  const blocks = parseBody([
    '## The problem',
    '',
    'Line one',
    'line two with [a link](https://example.com) and <script>alert(1)</script>.',
    '',
    '- first',
    '- second',
    '  continued',
    '',
    '1. one',
    '2. two',
  ].join('\n'))
  assert.deepEqual(blocks, [
    { kind: 'p', inlines: [{ kind: 'text', text: 'The problem' }] },
    { kind: 'p', inlines: [
      { kind: 'text', text: 'Line one line two with ' },
      { kind: 'link', text: 'a link', href: 'https://example.com' },
      { kind: 'text', text: ' and <script>alert(1)</script>.' },
    ] },
    { kind: 'ul', items: [[{ kind: 'text', text: 'first' }], [{ kind: 'text', text: 'second continued' }]] },
    { kind: 'ol', items: [[{ kind: 'text', text: 'one' }], [{ kind: 'text', text: 'two' }]] },
  ])
})

test('body links that are not https are errors and stay as text', () => {
  const errors: string[] = []
  const blocks = parseBody('See [this](javascript:alert(1)) and [that](http://x.com).', (e) => errors.push(e))
  assert.equal(errors.length, 2)
  assert.ok(blocks[0].kind === 'p' && blocks[0].inlines.every((inline) => inline.kind === 'text'))
  assert.ok(has(errorsFor(valid, '[x](http://x.com)'), 'body link'))
})

test('projects sort by order, then title; files without an order come last', () => {
  const make = (title: string, order: number | null) => ({ title, order }) as Project
  const sorted = sortProjects([make('Zed', null), make('Beta', 2), make('Alpha', null), make('Gamma', 1)])
  assert.deepEqual(sorted.map((p) => p.title), ['Gamma', 'Beta', 'Alpha', 'Zed'])
})
