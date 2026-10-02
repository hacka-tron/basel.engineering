import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { COPIED_TEXT, copyFeedback, copyText, EMAIL } from './contact.ts'

const fallbackMustNotRun = (): boolean => {
  throw new Error('the execCommand fallback should not run')
}

test('the Clipboard API copies the text and the fallback is not used', async () => {
  const written: string[] = []
  const ok = await copyText('a@b.c', { writeText: async (text) => { written.push(text) }, legacyCopy: fallbackMustNotRun })
  assert.equal(ok, true)
  assert.deepEqual(written, ['a@b.c'])
})

test('a rejected Clipboard API (permission denied) falls back to the execCommand copy', async () => {
  const fallback: string[] = []
  const ok = await copyText('a@b.c', {
    writeText: async () => { throw new Error('NotAllowedError') },
    legacyCopy: (text) => { fallback.push(text); return true },
  })
  assert.equal(ok, true)
  assert.deepEqual(fallback, ['a@b.c'])
})

test('a missing Clipboard API (insecure context, old browser) goes straight to the fallback', async () => {
  const ok = await copyText('a@b.c', { writeText: undefined, legacyCopy: () => true })
  assert.equal(ok, true)
})

test('when both the Clipboard API and the fallback fail, copying reports failure', async () => {
  const ok = await copyText('a@b.c', { writeText: async () => { throw new Error('denied') }, legacyCopy: () => false })
  assert.equal(ok, false)
})

test('a fallback that throws counts as a failure, never an unhandled error', async () => {
  const ok = await copyText('a@b.c', {
    writeText: async () => { throw new Error('denied') },
    legacyCopy: () => { throw new Error('execCommand blew up') },
  })
  assert.equal(ok, false)
})

test('success says Email copied for 2s; failure shows the address itself for 5s', () => {
  assert.equal(COPIED_TEXT, 'Email copied')
  assert.deepEqual(copyFeedback(true), { text: 'Email copied', ms: 2000 })
  assert.deepEqual(copyFeedback(false), { text: EMAIL, ms: 5000 })
})

test('the address stays out of the static HTML (bundle only)', () => {
  const html = readFileSync(new URL('../../index.html', import.meta.url), 'utf8')
  assert.equal(html.includes(EMAIL), false)
  assert.match(EMAIL, /^[^@\s]+@[^@\s]+\.[a-z]+$/)
})
