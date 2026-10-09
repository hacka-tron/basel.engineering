import assert from 'node:assert/strict'
import { test } from 'node:test'
import { initials, placeholderColors, portfolioHeading, PORTFOLIO_DETAILS_HINT, PORTFOLIO_EMPTY_TEXT, PORTFOLIO_STATUS_HINT, questionForProject, stackPreview } from './portfolioView.ts'

test('copy matches the spec', () => {
  assert.equal(PORTFOLIO_DETAILS_HINT, 'Select a project for details')
  assert.equal(PORTFOLIO_STATUS_HINT, 'Select a project')
  assert.equal(PORTFOLIO_EMPTY_TEXT, 'Projects are on their way. Ask the chat in the meantime.')
  assert.equal(questionForProject('JobPilot'), 'Tell me about JobPilot')
})

test('the heading counts projects, singular for one, plain when empty', () => {
  assert.equal(portfolioHeading(0), 'Projects')
  assert.equal(portfolioHeading(1), 'Projects · 1')
  assert.equal(portfolioHeading(4), 'Projects · 4')
})

test('cards show the first three stack tags, then +N', () => {
  assert.deepEqual(stackPreview(['A', 'B']), { shown: ['A', 'B'], rest: 0 })
  assert.deepEqual(stackPreview(['A', 'B', 'C', 'D', 'E']), { shown: ['A', 'B', 'C'], rest: 2 })
})

test('placeholder thumbnails are stable per project and differ between projects', () => {
  assert.deepEqual(placeholderColors('jobpilot'), placeholderColors('jobpilot'))
  assert.notDeepEqual(placeholderColors('jobpilot'), placeholderColors('ledger'))
  assert.match(placeholderColors('jobpilot').from, /^hsl\(\d+ 45% 34%\)$/)
})

test('initials: first letters of the first two words, or the first letter', () => {
  assert.equal(initials('Job Pilot'), 'JP')
  assert.equal(initials('ledger-sync tool'), 'LS')
  assert.equal(initials('JobPilot'), 'J')
  assert.equal(initials('  '), '')
})
