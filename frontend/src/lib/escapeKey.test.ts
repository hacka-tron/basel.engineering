import assert from 'node:assert/strict'
import { test } from 'node:test'
import { tooltipShowing } from './escapeKey.ts'

const active = (focusVisible: boolean) => ({ matches: (selector: string) => selector === ':focus-visible' && focusVisible })
const control = (inside: unknown) => ({ contains: (node: never) => node === inside })

test('an open press/long-press tooltip takes the Escape', () => {
  assert.equal(tooltipShowing(true, null, null), true)
})

test('keyboard focus on the control (CSS focus-visible tooltip) takes the Escape', () => {
  const el = active(true)
  assert.equal(tooltipShowing(false, control(el), el), true)
})

test('a mouse-focused control, focus elsewhere, or no control leaves Escape alone', () => {
  const mouseFocused = active(false)
  assert.equal(tooltipShowing(false, control(mouseFocused), mouseFocused), false)
  assert.equal(tooltipShowing(false, control(active(true)), active(true)), false)
  assert.equal(tooltipShowing(false, null, active(true)), false)
  assert.equal(tooltipShowing(false, control(null), null), false)
})
