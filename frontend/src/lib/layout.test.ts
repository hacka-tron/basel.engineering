import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { DESKTOP_QUERY, isPhoneScreen, PHONE_LANDSCAPE_VIEWPORT, ROTATE_QUERY, showsRotateScreen } from './layout.ts'

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

test('the rotate screen only covers the phone layout, so rotating back never switches layouts', () => {
  // The landscape viewport's height and width limits are max-md's second clause.
  const maxMd = variantQuery('max-md') ?? ''
  assert.ok(maxMd.endsWith('(height <= 500px) and (width < 1024px)'), maxMd)
  assert.equal(PHONE_LANDSCAPE_VIEWPORT, `(orientation: landscape) and (height <= 500px) and (width < 1024px)`)
  assert.equal(ROTATE_QUERY, `${PHONE_LANDSCAPE_VIEWPORT} and (pointer: coarse)`)
  // No CSS copy of the rotate query: the screen-size check cannot be written
  // in CSS, so the rotate screen is shown by React, never by a media query.
  assert.equal(variantQuery('phone-landscape'), undefined)
})

test('phone screens are at most 500px on the short side, either orientation', () => {
  assert.ok(isPhoneScreen({ width: 393, height: 852 }))
  assert.ok(isPhoneScreen({ width: 932, height: 430 }))
  assert.ok(isPhoneScreen({ width: 320, height: 568 }))
  assert.ok(!isPhoneScreen({ width: 744, height: 1133 })) // iPad mini
  assert.ok(!isPhoneScreen({ width: 1280, height: 800 })) // Android tablet
  assert.ok(!isPhoneScreen({ width: 1920, height: 1080 }))
})

test('the rotate screen: phones sideways only, never tablets with the keyboard open or desktops', () => {
  const phone = { width: 375, height: 667 }
  const tablet = { width: 1280, height: 800 }
  const monitor = { width: 1920, height: 1080 }
  const show = (landscapeTouch: boolean, landscapeViewport: boolean, screen: { width: number; height: number }, preview = false) =>
    showsRotateScreen({ landscapeTouch, landscapeViewport, screen, preview })
  assert.equal(show(true, true, phone), true)
  // A phone upright.
  assert.equal(show(false, false, phone), false)
  // A tablet whose on-screen keyboard leaves a short landscape viewport.
  assert.equal(show(true, true, tablet), false)
  // A short desktop window (mouse: not coarse).
  assert.equal(show(false, true, monitor), false)
  // The dev phone preview's landscape frames in a desktop browser.
  assert.equal(show(false, true, monitor, true), true)
  assert.equal(show(false, false, monitor, true), false)
})
