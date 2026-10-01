/**
 * The layout media queries. `src/index.css` redefines Tailwind's `md`/`max-md`
 * variants with the exact DESKTOP_QUERY string (a unit test checks they match),
 * and `useMediaQuery` uses it to decide which diagram to mount. See
 * project/MOBILE_DESIGN.md "Phones are portrait only".
 */

/**
 * Two-pane desktop layout: 768px wide and taller than a phone held sideways,
 * or 1024px wide. The height clause keeps every phone held sideways (896x414
 * included) in the phone layout, under the rotate screen, so turning it back
 * upright never switches layouts (the diagram stays mounted as it was).
 */
export const DESKTOP_QUERY = '(width >= 768px) and (height > 500px), (width >= 1024px)'

/**
 * A phone-shaped viewport held sideways: wider than tall, at most 500px tall
 * and under 1024px wide (every phone sideways is 568x320 to 932x430). Its size
 * limits are the landscape part of `max-md`, so the phone layout is always what
 * sits under the rotate screen. Used as is only by the dev phone preview, whose
 * frames have no device orientation of their own; real phones use
 * ROTATE_QUERY plus the device orientation.
 */
export const PHONE_LANDSCAPE_VIEWPORT = '(orientation: landscape) and (height <= 500px) and (width < 1024px)'

/**
 * The rotate screen's size guard: a phone-sized viewport (at most 500px tall,
 * under 1024px wide) on a touch screen. `pointer: coarse` keeps a short desktop
 * browser window (say 900x450, mouse or trackpad) usable. Deliberately no
 * `(orientation: landscape)`: on Android the on-screen keyboard shrinks the
 * viewport (`interactive-widget=resizes-content`), so a 360x640 phone held
 * upright becomes about 360x280 while typing, which the viewport calls
 * landscape. Whether the phone is sideways comes from the device instead
 * (`isDeviceLandscape`).
 */
export const ROTATE_QUERY = '(height <= 500px) and (width < 1024px) and (pointer: coarse)'

/**
 * A phone's screen is at most this many CSS px on its short side (phones are
 * 320 to 440, the smallest tablets about 600). Unlike the viewport, the screen
 * does not shrink when an on-screen keyboard opens, so a tablet whose keyboard
 * leaves a 500px-tall viewport is not mistaken for a phone. It is re-read
 * whenever the orientation or the window size changes (a foldable unfolding
 * changes its screen).
 */
export const PHONE_SCREEN_MAX_SHORT_SIDE = 500

export function isPhoneScreen(screen: { width: number; height: number }): boolean {
  return Math.min(screen.width, screen.height) <= PHONE_SCREEN_MAX_SHORT_SIDE
}

/**
 * Whether the device itself is held sideways (never the viewport's shape, which
 * the keyboard changes): `screen.orientation.type`, or the legacy
 * `window.orientation` (90 or -90 sideways) where that API is missing (iOS
 * before 16.4). Null when neither exists.
 */
export function isDeviceLandscape({ orientationType, windowOrientation }: {
  orientationType?: string | null
  windowOrientation?: number | null
}): boolean | null {
  if (orientationType) return orientationType.startsWith('landscape')
  if (typeof windowOrientation === 'number') return Math.abs(windowOrientation) === 90
  return null
}

/**
 * Whether to show "turn your phone upright" instead of the app: the device is
 * sideways, the viewport is phone-sized on a touch screen (ROTATE_QUERY), and
 * the screen is phone-sized. `preview` (the dev phone preview's landscape
 * frames, `?phone`) uses the frame's own shape instead
 * (PHONE_LANDSCAPE_VIEWPORT), since a desktop browser has no touch screen, a
 * monitor-sized screen and its own device orientation.
 */
export function showsRotateScreen({ deviceLandscape, phoneViewport, landscapeViewport, phoneScreen, preview }: {
  deviceLandscape: boolean | null
  phoneViewport: boolean
  landscapeViewport: boolean
  /** isPhoneScreen(window.screen) */
  phoneScreen: boolean
  preview: boolean
}): boolean {
  if (preview) return landscapeViewport
  return deviceLandscape === true && phoneViewport && phoneScreen
}
