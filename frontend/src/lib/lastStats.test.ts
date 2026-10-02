import assert from 'node:assert/strict'
import { test } from 'node:test'
import { compactDuration, lastStatsDetails, lastStatsParts } from './lastStats.ts'

test('before any answer the readout has no number', () => {
  assert.equal(lastStatsParts(null).timing, '—')
})

test('a generated answer shows its time to first token', () => {
  assert.equal(lastStatsParts({ firstTokenMs: 612, totalMs: 2400, cacheStatus: 'miss' }).timing, '612ms')
})

test('an answer-cache hit shows only the number; the cache hit is left to the tooltip', () => {
  const parts = lastStatsParts({ firstTokenMs: 180, totalMs: 175, cacheStatus: 'hit' })
  assert.equal(parts.timing, '180ms')
  assert.doesNotMatch(`${parts.timing} ${parts.description}`, /cache/i)
})

test('with no token the whole-request time is labelled total, never first token', () => {
  const label = lastStatsParts({ firstTokenMs: null, totalMs: 812, cacheStatus: 'miss' }).timing
  assert.equal(label, 'total 812ms')
  assert.doesNotMatch(label, /first token/)
})

test('the visible text never says first token; the description does', () => {
  for (const stats of [null, { firstTokenMs: 90, totalMs: 120, cacheStatus: 'hit' as const }]) {
    const parts = lastStatsParts(stats)
    assert.doesNotMatch(parts.timing, /first token/)
    assert.match(parts.description, /time to first token/i)
  }
  assert.match(lastStatsParts({ firstTokenMs: null, totalMs: 9, cacheStatus: 'miss' }).description, /total/i)
})

test('parts carry the timing, the phone reading and the description', () => {
  assert.deepEqual(lastStatsParts({ firstTokenMs: 1234, totalMs: 1300, cacheStatus: 'hit' }), { timing: '1234ms', short: '1.2s', description: 'Time to first token' })
  assert.deepEqual(lastStatsParts({ firstTokenMs: null, totalMs: 900, cacheStatus: 'hit' }), { timing: 'total 900ms', short: '900ms', description: 'Total request time (no answer text was generated)' })
})

test('details explain the number in at most three short lines', () => {
  const miss = lastStatsDetails({ firstTokenMs: 312, totalMs: 2400, cacheStatus: 'miss' })
  assert.deepEqual(miss, ['Time to first token: 312ms. How long until the first word of the answer arrived.', 'Whole answer: 2400ms.'])
  const hit = lastStatsDetails({ firstTokenMs: 180, totalMs: 175, cacheStatus: 'hit' })
  assert.equal(hit[1], 'Served from the answer cache.')
  assert.match(lastStatsDetails({ firstTokenMs: null, totalMs: 812, cacheStatus: 'miss' })[0], /812ms/)
  assert.equal(lastStatsDetails(null).length, 1)
})

test('phone readings: ms under a second, tenths of a second up to 99.9s, whole seconds after', () => {
  assert.equal(compactDuration(0), '0ms')
  assert.equal(compactDuration(312), '312ms')
  assert.equal(compactDuration(999), '999ms')
  assert.equal(compactDuration(999.6), '1.0s')
  assert.equal(compactDuration(1840), '1.8s')
  assert.equal(compactDuration(9950), '10.0s')
  assert.equal(compactDuration(12345), '12.3s')
  assert.equal(compactDuration(99949), '99.9s')
  assert.equal(compactDuration(99950), '100s')
  assert.equal(compactDuration(999499), '999s')
  assert.equal(compactDuration(999500), '999s+')
})

test('a phone reading is at most five characters for any value, so the footer fits at 280px', () => {
  for (const ms of [0, 7, 999, 999.6, 1000, 1840, 9949, 9950, 12345, 99949, 99950, 999499, 999500, 1e9, Number.MAX_SAFE_INTEGER]) {
    assert.ok(compactDuration(ms).length <= 5, `${ms} -> ${compactDuration(ms)}`)
  }
})

test('a missing or broken number reads as a dash, never NaN', () => {
  assert.equal(compactDuration(Number.NaN), '—')
  assert.equal(compactDuration(-5), '—')
  assert.equal(compactDuration(Number.POSITIVE_INFINITY), '—')
})

test('the phone reading drops the word total; the description and long reading keep it', () => {
  const parts = lastStatsParts({ firstTokenMs: null, totalMs: 12345, cacheStatus: 'miss' })
  assert.equal(parts.short, '12.3s')
  assert.equal(parts.timing, 'total 12345ms')
  assert.match(parts.description, /total/i)
  assert.match(lastStatsDetails({ firstTokenMs: null, totalMs: 12345, cacheStatus: 'miss' })[0], /Total time: 12345ms/)
})

test('before any answer the phone reading is a dash too', () => {
  assert.equal(lastStatsParts(null).short, '—')
})
