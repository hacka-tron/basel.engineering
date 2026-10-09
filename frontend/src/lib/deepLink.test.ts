import assert from 'node:assert/strict'
import { test } from 'node:test'
import { linkSearch, parseDeepLink } from './deepLink.ts'

const slugs = ['goalbuddy']
const none = { corpus: null, project: null }

test('?project=<slug> opens About Basel with that project and its grid', () => {
  const link = { corpus: 'basel', project: 'goalbuddy', projects: true }
  assert.deepEqual(parseDeepLink('?project=goalbuddy', slugs), link)
  assert.deepEqual(parseDeepLink('?topic=system&project=goalbuddy', slugs), link)
})

test('?topic= picks a topic; the retired ?topic=portfolio opens About Basel with its grid', () => {
  assert.deepEqual(parseDeepLink('?topic=portfolio', slugs), { corpus: 'basel', project: null, projects: true })
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
  // No published project: an old Portfolio link or a project link reads as no link.
  assert.deepEqual(parseDeepLink('?topic=portfolio', []), none)
  assert.deepEqual(parseDeepLink('?project=goalbuddy', []), none)
})

test('linkSearch writes what is shown: the project, else the topic; About Basel has no query', () => {
  assert.equal(linkSearch('', { corpus: 'basel', project: 'goalbuddy' }), '?project=goalbuddy')
  assert.equal(linkSearch('?project=goalbuddy', { corpus: 'system', project: null }), '?topic=system')
  assert.equal(linkSearch('?topic=portfolio', { corpus: 'basel', project: null }), '')
  assert.equal(linkSearch('?topic=system', { corpus: 'basel', project: null }), '')
  assert.equal(linkSearch('?utm_source=x&topic=system', { corpus: 'basel', project: 'goalbuddy' }), '?utm_source=x&project=goalbuddy')
  assert.equal(linkSearch('?utm_source=x&project=goalbuddy', { corpus: 'basel', project: null }), '?utm_source=x')
})

test('a written link reads back as the same view', () => {
  assert.deepEqual(parseDeepLink(linkSearch('', { corpus: 'basel', project: 'goalbuddy' }), slugs), { corpus: 'basel', project: 'goalbuddy', projects: true })
  assert.deepEqual(parseDeepLink(linkSearch('', { corpus: 'system', project: null }), slugs), { corpus: 'system', project: null })
  assert.deepEqual(parseDeepLink(linkSearch('', { corpus: 'basel', project: null }), slugs), none)
})
