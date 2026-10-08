import assert from 'node:assert/strict'
import { test } from 'node:test'
import { linkSearch, parseDeepLink } from './deepLink.ts'

const slugs = ['goalbuddy']
const none = { corpus: null, project: null }

test('?project=<slug> opens the Portfolio with that project', () => {
  assert.deepEqual(parseDeepLink('?project=goalbuddy', slugs), { corpus: 'portfolio', project: 'goalbuddy' })
  assert.deepEqual(parseDeepLink('?topic=system&project=goalbuddy', slugs), { corpus: 'portfolio', project: 'goalbuddy' })
})

test('?topic= picks a topic', () => {
  assert.deepEqual(parseDeepLink('?topic=portfolio', slugs), { corpus: 'portfolio', project: null })
  assert.deepEqual(parseDeepLink('?topic=system', slugs), { corpus: 'system', project: null })
  assert.deepEqual(parseDeepLink('?topic=basel', slugs), { corpus: 'basel', project: null })
})

test('unknown, draft or hidden values are ignored', () => {
  assert.deepEqual(parseDeepLink('', slugs), none)
  assert.deepEqual(parseDeepLink('?project=_example', slugs), none)
  assert.deepEqual(parseDeepLink('?project=GoalBuddy', slugs), none)
  assert.deepEqual(parseDeepLink('?project=__proto__', slugs), none)
  assert.deepEqual(parseDeepLink('?topic=admin', slugs), none)
  assert.deepEqual(parseDeepLink('?project=nope&topic=system', slugs), { corpus: 'system', project: null })
  // No published project: the Portfolio topic is hidden, so a link to it reads as no link.
  assert.deepEqual(parseDeepLink('?topic=portfolio', []), none)
  assert.deepEqual(parseDeepLink('?project=goalbuddy', []), none)
})

test('linkSearch writes what is shown: the project, else the topic; About Basel has no query', () => {
  assert.equal(linkSearch('', { corpus: 'portfolio', project: 'goalbuddy' }), '?project=goalbuddy')
  assert.equal(linkSearch('?project=goalbuddy', { corpus: 'portfolio', project: null }), '?topic=portfolio')
  assert.equal(linkSearch('?project=goalbuddy', { corpus: 'system', project: null }), '?topic=system')
  assert.equal(linkSearch('?topic=system', { corpus: 'basel', project: null }), '')
  assert.equal(linkSearch('?utm_source=x&topic=system', { corpus: 'portfolio', project: 'goalbuddy' }), '?utm_source=x&project=goalbuddy')
  assert.equal(linkSearch('?utm_source=x&project=goalbuddy', { corpus: 'basel', project: null }), '?utm_source=x')
})

test('a written link reads back as the same view', () => {
  for (const link of [{ corpus: 'portfolio', project: 'goalbuddy' }, { corpus: 'portfolio', project: null }, { corpus: 'system', project: null }] as const) {
    assert.deepEqual(parseDeepLink(linkSearch('', link), slugs), link)
  }
  assert.deepEqual(parseDeepLink(linkSearch('', { corpus: 'basel', project: null }), slugs), none)
})
