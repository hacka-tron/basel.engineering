import assert from 'node:assert/strict'
import { test } from 'node:test'
import { galleryControlsShown, lightboxKey, nearestIndex } from './gallery.ts'

test('the current visual is the one nearest the scroller\'s left edge', () => {
  assert.equal(nearestIndex([0, 300, 600], 0, false), 0)
  assert.equal(nearestIndex([0, 300, 600], 280, false), 1)
  assert.equal(nearestIndex([0, 300, 600], 449, false), 1)
  assert.equal(nearestIndex([], 0, false), 0)
})

test('scrolled to the end, the last visual counts as current even if it never reaches the left edge', () => {
  assert.equal(nearestIndex([0, 300, 600], 420, true), 2)
})

test('lightbox keys: Escape closes; arrows move and stop at the ends; other keys are ignored', () => {
  assert.deepEqual(lightboxKey('Escape', 1, 3), { action: 'close' })
  assert.deepEqual(lightboxKey('ArrowRight', 1, 3), { action: 'show', index: 2 })
  assert.deepEqual(lightboxKey('ArrowLeft', 1, 3), { action: 'show', index: 0 })
  assert.equal(lightboxKey('ArrowRight', 2, 3), null)
  assert.equal(lightboxKey('ArrowLeft', 0, 3), null)
  assert.equal(lightboxKey('Enter', 1, 3), null)
})

test('dots, count and arrows are shown only for two or more visuals', () => {
  assert.equal(galleryControlsShown(0), false)
  assert.equal(galleryControlsShown(1), false)
  assert.equal(galleryControlsShown(2), true)
})
