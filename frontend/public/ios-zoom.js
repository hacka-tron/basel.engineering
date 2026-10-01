// iOS Safari zooms the page when a text field under 16px is focused. The ask
// box matches the 13px message text, so on iOS only, cap the scale: iOS still
// allows pinch-zoom (it ignores maximum-scale for user gestures), but no
// longer zooms on focus. Not applied elsewhere, because Android browsers would
// honour it and block pinch-zoom.
//
// A same-origin file rather than an inline <script> so the Content-Security-
// Policy can use script-src 'self' without hashes (docs/DESIGN.md §11). It is
// loaded as a classic, parser-blocking script right after the viewport <meta>
// in index.html, so it still runs before the body is parsed and before first
// paint, exactly as the inline version did. Not a module, not deferred: Vite
// copies it from public/ unchanged.
if (/iP(hone|od|ad)/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) {
  var viewport = document.querySelector('meta[name="viewport"]')
  if (viewport) viewport.setAttribute('content', viewport.getAttribute('content') + ', maximum-scale=1')
}
