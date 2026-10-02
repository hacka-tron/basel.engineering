import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

// The component is TSX (not runnable under node --test), so this pins the
// owner-approved wording in its source (spec §4).
const source = readFileSync(new URL('./OpenToWork.tsx', import.meta.url), 'utf8')

test('the footer item uses the approved wording, verbatim', () => {
  for (const text of [
    "'Open to work'",
    "'Open to full-time work and freelancing'",
    "'Talking to teams about full-time roles and taking on freelance projects. Copy my email and say hi.'",
    "'Copy email'",
  ]) {
    assert.ok(source.includes(text), `missing ${text}`)
  }
})

test('the portfolio link waits for the portfolio PR', () => {
  assert.doesNotMatch(source, /See portfolio/i)
})
