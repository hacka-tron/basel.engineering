import { PHONE_LANDSCAPE_VIEWPORT, ROTATE_QUERY, showsRotateScreen } from '../lib/layout'
import { useMediaQuery } from './useMediaQuery'

// The dev phone preview (phone-preview.html) loads its landscape frames with
// `?phone`, because a desktop browser has neither a touch screen nor a
// phone-sized screen. Dev server only; production ignores it.
const PREVIEW = import.meta.env.DEV && new URLSearchParams(window.location.search).has('phone')

/**
 * True while a phone is held sideways: the app shows "turn your phone upright"
 * instead of itself (project/MOBILE_DESIGN.md "Phones are portrait only").
 */
export function useRotateScreen(): boolean {
  const landscapeTouch = useMediaQuery(ROTATE_QUERY)
  const landscapeViewport = useMediaQuery(PHONE_LANDSCAPE_VIEWPORT)
  return showsRotateScreen({ landscapeTouch, landscapeViewport, screen: window.screen, preview: PREVIEW })
}
