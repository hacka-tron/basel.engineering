/**
 * The layout media queries, shared by CSS and JS. `src/index.css` redefines
 * Tailwind's `md`/`max-md` variants and adds `phone-landscape` with these
 * exact strings (a unit test checks they match), and `useMediaQuery` uses
 * them to decide which diagram to mount. See project/MOBILE_DESIGN.md
 * "Landscape phones".
 */

/** Two-pane desktop layout: 768px wide and taller than a phone held sideways, or 1024px wide. */
export const DESKTOP_QUERY = '(width >= 768px) and (height > 500px), (width >= 1024px)'

/** A phone held sideways: the phone layout, with the wide diagram and compact chrome. */
export const PHONE_LANDSCAPE_QUERY = '(orientation: landscape) and (height <= 500px) and (width < 1024px)'
