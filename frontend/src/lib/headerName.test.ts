import assert from 'node:assert/strict'
import { test } from 'node:test'
import { MIN_GAP_PX, RETURN_MARGIN_PX, shouldShowFullName } from './headerName.ts'

test('full name stays while it fits, even exactly', () => {
  assert.equal(shouldShowFullName(true, 200, 200, MIN_GAP_PX), true)
  assert.equal(shouldShowFullName(true, 200, 300, MIN_GAP_PX), true)
})

test('full name gives way as soon as it does not fit', () => {
  assert.equal(shouldShowFullName(true, 200, 199, MIN_GAP_PX), false)
})

test('short name needs a margin before the full name returns', () => {
  assert.equal(shouldShowFullName(false, 200, 200, MIN_GAP_PX), false)
  assert.equal(shouldShowFullName(false, 200, 200 + RETURN_MARGIN_PX - 1, MIN_GAP_PX), false)
  assert.equal(shouldShowFullName(false, 200, 200 + RETURN_MARGIN_PX, MIN_GAP_PX), true)
})

test('an unmeasured (zero) layout keeps the current choice', () => {
  assert.equal(shouldShowFullName(true, 0, 300), true)
  assert.equal(shouldShowFullName(false, 200, 0), false)
})

test('with no row gap the name keeps a minimum gap before it must shorten', () => {
  assert.equal(shouldShowFullName(true, 200, 200 + MIN_GAP_PX, 0), true)
  assert.equal(shouldShowFullName(true, 200, 200 + MIN_GAP_PX - 1, 0), false)
})

test('a row gap of 16px already provides the minimum gap', () => {
  assert.equal(shouldShowFullName(true, 200, 200, MIN_GAP_PX), true)
  assert.equal(shouldShowFullName(true, 200, 199, MIN_GAP_PX), false)
})
