import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { DESKTOP_QUERY, isDeviceLandscape, isPhoneScreen, PHONE_LANDSCAPE_VIEWPORT, ROTATE_QUERY, showsRotateScreen } from './layout.ts'

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
  assert.equal(ROTATE_QUERY, '(height <= 500px) and (width < 1024px) and (pointer: coarse)')
  // Orientation comes from the device, not the viewport (the keyboard changes its shape).
  assert.ok(!ROTATE_QUERY.includes('orientation'))
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

test('device orientation: screen.orientation first, window.orientation as the fallback', () => {
  assert.equal(isDeviceLandscape({ orientationType: 'landscape-primary' }), true)
  assert.equal(isDeviceLandscape({ orientationType: 'landscape-secondary', windowOrientation: 0 }), true)
  assert.equal(isDeviceLandscape({ orientationType: 'portrait-primary', windowOrientation: 90 }), false)
  // iOS before 16.4: no screen.orientation.
  assert.equal(isDeviceLandscape({ windowOrientation: 90 }), true)
  assert.equal(isDeviceLandscape({ windowOrientation: -90 }), true)
  assert.equal(isDeviceLandscape({ windowOrientation: 0 }), false)
  assert.equal(isDeviceLandscape({ windowOrientation: 180 }), false)
  assert.equal(isDeviceLandscape({}), null)
})

test('the rotate screen: phones sideways only, never while typing upright, on tablets or desktops', () => {
  const show = (deviceLandscape: boolean | null, phoneViewport: boolean, phoneScreen: boolean, landscapeViewport = false, preview = false) =>
    showsRotateScreen({ deviceLandscape, phoneViewport, landscapeViewport, phoneScreen, preview })
  // A phone held sideways.
  assert.equal(show(true, true, true, true), true)
  // A phone held upright, Android keyboard open: the viewport (360x280) is
  // short and wider than tall, but the device is portrait.
  assert.equal(show(false, true, true, true), false)
  // A phone upright, keyboard closed (393x852: not a phone-sized landscape viewport).
  assert.equal(show(false, false, true), false)
  // Unknown device orientation: never block the app.
  assert.equal(show(null, true, true, true), false)
  // A tablet sideways whose keyboard leaves a short viewport: not a phone screen.
  assert.equal(show(true, true, false, true), false)
  // A short desktop window (mouse: not coarse), monitor-sized screen.
  assert.equal(show(true, false, false, true), false)
  // The dev phone preview's landscape frames in a desktop browser.
  assert.equal(show(true, false, false, true, true), true)
  assert.equal(show(true, false, false, false, true), false)
})
