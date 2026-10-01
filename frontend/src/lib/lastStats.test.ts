import assert from 'node:assert/strict'
import { test } from 'node:test'
import { lastStatsDetails, lastStatsParts } from './lastStats.ts'

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

test('parts carry the timing and its description', () => {
  assert.deepEqual(lastStatsParts({ firstTokenMs: 1234, totalMs: 1300, cacheStatus: 'hit' }), { timing: '1234ms', description: 'Time to first token' })
  assert.deepEqual(lastStatsParts({ firstTokenMs: null, totalMs: 900, cacheStatus: 'hit' }), { timing: 'total 900ms', description: 'Total request time (no answer text was generated)' })
})

test('details explain the number in at most three short lines', () => {
  const miss = lastStatsDetails({ firstTokenMs: 312, totalMs: 2400, cacheStatus: 'miss' })
  assert.deepEqual(miss, ['Time to first token: 312ms. How long until the first word of the answer arrived.', 'Whole answer: 2400ms.'])
  const hit = lastStatsDetails({ firstTokenMs: 180, totalMs: 175, cacheStatus: 'hit' })
  assert.equal(hit[1], 'Served from the answer cache.')
  assert.match(lastStatsDetails({ firstTokenMs: null, totalMs: 812, cacheStatus: 'miss' })[0], /812ms/)
  assert.equal(lastStatsDetails(null).length, 1)
})
