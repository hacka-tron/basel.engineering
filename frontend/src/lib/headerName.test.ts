import assert from 'node:assert/strict'
import { test } from 'node:test'
import { MIN_GAP_PX, nextHeaderFit, RETURN_MARGIN_PX, shouldShowFullName } from './headerName.ts'

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

const widths = { fullName: 180, shortName: 90, contactText: 110, contactIcon: 44 }

test('header fit: full name with the Contact label while both fit', () => {
  assert.equal(nextHeaderFit('full', widths, 290, MIN_GAP_PX), 'full')
  assert.equal(nextHeaderFit('full', widths, 289, MIN_GAP_PX), 'short')
})

test('header fit: short name keeps the label, then Contact becomes an icon', () => {
  assert.equal(nextHeaderFit('full', widths, 200, MIN_GAP_PX), 'short')
  assert.equal(nextHeaderFit('short', widths, 199, MIN_GAP_PX), 'icon')
  assert.equal(nextHeaderFit('short', widths, 100, MIN_GAP_PX), 'icon')
})

test('header fit: a roomier step returns only past the margin', () => {
  assert.equal(nextHeaderFit('icon', widths, 200, MIN_GAP_PX), 'icon')
  assert.equal(nextHeaderFit('icon', widths, 200 + RETURN_MARGIN_PX, MIN_GAP_PX), 'short')
  assert.equal(nextHeaderFit('short', widths, 290 + RETURN_MARGIN_PX - 1, MIN_GAP_PX), 'short')
  assert.equal(nextHeaderFit('short', widths, 290 + RETURN_MARGIN_PX, MIN_GAP_PX), 'full')
  assert.equal(nextHeaderFit('icon', widths, 400, MIN_GAP_PX), 'full')
})

test('header fit: reserves the minimum gap when the row gap is smaller', () => {
  assert.equal(nextHeaderFit('full', widths, 290 + MIN_GAP_PX, 0), 'full')
  assert.equal(nextHeaderFit('full', widths, 290 + MIN_GAP_PX - 1, 0), 'short')
})

test('header fit: an unmeasured layout keeps the current step', () => {
  assert.equal(nextHeaderFit('short', { ...widths, contactIcon: 0 }, 400, MIN_GAP_PX), 'short')
  assert.equal(nextHeaderFit('icon', widths, 0, MIN_GAP_PX), 'icon')
})
