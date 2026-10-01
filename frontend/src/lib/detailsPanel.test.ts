import assert from 'node:assert/strict'
import { test } from 'node:test'
import { portraitDetailsState } from './detailsPanel.ts'

test('locked whenever no component is selected, even if it was left open', () => {
  assert.equal(portraitDetailsState(false, false), 'locked')
  assert.equal(portraitDetailsState(false, true), 'locked')
})

test('follows the open/collapse choice once a component is selected', () => {
  assert.equal(portraitDetailsState(true, true), 'open')
  assert.equal(portraitDetailsState(true, false), 'collapsed')
})
