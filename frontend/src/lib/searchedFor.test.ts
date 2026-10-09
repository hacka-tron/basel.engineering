import assert from 'node:assert/strict'
import { test } from 'node:test'
import { searchedForText } from './searchedFor.ts'

test('About This System shows the rewritten query', () => {
  assert.equal(searchedForText('system', 'how does caching work'), 'how does caching work')
})

test('About Basel never shows it', () => {
  assert.equal(searchedForText('basel', 'where did he study'), null)
  assert.equal(searchedForText('basel', 'which projects'), null)
})

test('nothing to show without a rewritten query', () => {
  assert.equal(searchedForText('system', undefined), null)
  assert.equal(searchedForText('system', ''), null)
})
