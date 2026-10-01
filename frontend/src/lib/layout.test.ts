import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { DESKTOP_QUERY, PHONE_LANDSCAPE_QUERY } from './layout.ts'

const css = readFileSync(new URL('../index.css', import.meta.url), 'utf8')

function variantQuery(name: string): string | undefined {
  return css.match(new RegExp(`@custom-variant ${name} \\{\\s*@media ([^{]+?) \\{`))?.[1]
}

test('the CSS md variant and the JS desktop query are the same', () => {
  assert.equal(variantQuery('md'), DESKTOP_QUERY)
})

test('max-md is the exact opposite of md', () => {
  assert.equal(variantQuery('max-md'), '(width < 768px), (height <= 500px) and (width < 1024px)')
})

test('the CSS phone-landscape variant and the JS query are the same', () => {
  assert.equal(variantQuery('phone-landscape'), PHONE_LANDSCAPE_QUERY)
})
