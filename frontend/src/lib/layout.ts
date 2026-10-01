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
 * and under 1024px wide. Every phone sideways matches (568x320 to 932x430);
 * tablets (at least 600px tall) and desktops do not at rest. It is the
 * landscape part of `max-md`, so the phone layout is always what sits under
 * the rotate screen.
 */
export const PHONE_LANDSCAPE_VIEWPORT = '(orientation: landscape) and (height <= 500px) and (width < 1024px)'

/**
 * The rotate screen's media query: a phone-shaped landscape viewport on a touch
 * screen. `pointer: coarse` keeps a short desktop browser window (say 900x450,
 * mouse or trackpad) usable.
 */
export const ROTATE_QUERY = `${PHONE_LANDSCAPE_VIEWPORT} and (pointer: coarse)`

/**
 * A phone's screen is at most this many CSS px on its short side (phones are
 * 320 to 440, the smallest tablets about 600). Unlike the viewport, the screen
 * does not shrink when an on-screen keyboard opens, so a tablet whose keyboard
 * leaves a 500px-tall landscape viewport is not mistaken for a phone, and a
 * zoomed-in desktop browser is excluded by `pointer: coarse` above.
 */
export const PHONE_SCREEN_MAX_SHORT_SIDE = 500

export function isPhoneScreen(screen: { width: number; height: number }): boolean {
  return Math.min(screen.width, screen.height) <= PHONE_SCREEN_MAX_SHORT_SIDE
}

/**
 * Whether to show "turn your phone upright" instead of the app: ROTATE_QUERY
 * matches on a phone-sized screen. `preview` (the dev phone preview's
 * landscape frames, `?phone`) skips the touch and screen checks, which a
 * desktop browser showing phone-sized frames can never pass.
 */
export function showsRotateScreen({ landscapeTouch, landscapeViewport, screen, preview }: {
  landscapeTouch: boolean
  landscapeViewport: boolean
  screen: { width: number; height: number }
  preview: boolean
}): boolean {
  return preview ? landscapeViewport : landscapeTouch && isPhoneScreen(screen)
}
