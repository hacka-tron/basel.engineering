import assert from 'node:assert/strict'
import { test } from 'node:test'
import { HEADER_FADE_PX, panIntoStrip, SHEET_COVER, scrollTopForItem, stripCenterY } from './detailsSheet.ts'

test('the sheet covers 80% of its region', () => {
  assert.equal(SHEET_COVER, 0.8)
})

test('the strip centre is the middle of the uncovered 20%, moved down by half the header fade', () => {
  assert.equal(HEADER_FADE_PX, 16)
  assert.ok(Math.abs(stripCenterY(500) - 58) < 1e-9) // 500 * 0.2 / 2 + 8
  assert.equal(stripCenterY(0), 8)
})

test('panning keeps the fitted zoom and x and puts the node centre in the strip', () => {
  const fit = { x: 10, y: 40, zoom: 0.75 }
  const panned = panIntoStrip(fit, 200, 500)
  assert.equal(panned.x, 10)
  assert.equal(panned.zoom, 0.75)
  assert.ok(Math.abs(panned.y - (58 - 150)) < 1e-9)
})

test('a card scrolls to 12px below the top of its list, never above zero', () => {
  assert.equal(scrollTopForItem(100, 50, 400), 438)
  assert.equal(scrollTopForItem(0, 50, 55), 0)
  assert.equal(scrollTopForItem(20, 0, 30, 0), 50)
})
