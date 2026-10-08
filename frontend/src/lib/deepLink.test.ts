import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseDeepLink } from './deepLink.ts'

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
