import assert from 'node:assert/strict'
import { test } from 'node:test'
import { lastStatsLabel, lastStatsParts } from './lastStats.ts'

test('before any answer the readout has no number', () => {
  assert.equal(lastStatsLabel(null), 'first token —')
})

test('a generated answer shows its time to first token', () => {
  assert.equal(lastStatsLabel({ firstTokenMs: 612, totalMs: 2400, cacheStatus: 'miss' }), 'first token 612ms')
})

test('an answer-cache hit is marked cached', () => {
  assert.equal(lastStatsLabel({ firstTokenMs: 180, totalMs: 175, cacheStatus: 'hit' }), 'first token 180ms · cached')
})

test('with no token the whole-request time is labelled total, never first token', () => {
  const label = lastStatsLabel({ firstTokenMs: null, totalMs: 812, cacheStatus: 'miss' })
  assert.equal(label, 'total 812ms')
  assert.doesNotMatch(label, /first token/)
})

test('parts keep the cache marker separate for the narrow layout', () => {
  assert.deepEqual(lastStatsParts({ firstTokenMs: 1234, totalMs: 1300, cacheStatus: 'hit' }), { timing: 'first token 1234ms', cached: true })
  assert.deepEqual(lastStatsParts({ firstTokenMs: null, totalMs: 900, cacheStatus: 'hit' }), { timing: 'total 900ms', cached: false })
})
