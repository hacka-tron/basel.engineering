import assert from 'node:assert/strict'
import { test } from 'node:test'
import { boundsOf, createRefitter, type Size } from './diagramFit.ts'

function setup(initial: Size) {
  let size = initial
  const fits: Size[] = []
  const queue = new Map<number, () => void>()
  let next = 0
  const refitter = createRefitter(() => size, (s) => fits.push(s), {
    request: (cb) => { queue.set(++next, cb); return next },
    cancel: (h) => { queue.delete(h) },
  })
  const frame = () => { const cbs = [...queue.values()]; queue.clear(); cbs.forEach((cb) => cb()) }
  return { refitter, fits, frame, resize: (s: Size) => { size = s } }
}

test('boundsOf covers fixed-size nodes', () => {
  assert.deepEqual(boundsOf([{ x: 0, y: 0 }, { x: 720, y: 380 }], 124, 42), { x: 0, y: 0, width: 844, height: 422 })
})

test('each resize refits to the current size, including widening back', () => {
  const t = setup({ width: 864, height: 494 })
  t.refitter.request(); t.frame()
  t.resize({ width: 375, height: 496 }); t.refitter.request(); t.frame()
  t.resize({ width: 864, height: 494 }); t.refitter.request(); t.frame()
  assert.deepEqual(t.fits.map((s) => s.width), [864, 375, 864])
})

test('a burst of notifications in one frame fits once', () => {
  const t = setup({ width: 600, height: 400 })
  t.refitter.request(); t.refitter.request(); t.refitter.request(); t.frame()
  assert.equal(t.fits.length, 1)
})

test('a hidden or unlaid-out container is skipped, then fits once measured', () => {
  const t = setup({ width: 0, height: 0 })
  t.refitter.request(); t.frame()
  assert.equal(t.fits.length, 0)
  t.resize({ width: 540, height: 494 }); t.refitter.request(); t.frame()
  assert.equal(t.fits.length, 1)
})

test('cancel drops a pending fit', () => {
  const t = setup({ width: 600, height: 400 })
  t.refitter.request(); t.refitter.cancel(); t.frame()
  assert.equal(t.fits.length, 0)
})
