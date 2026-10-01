import { useEffect, useState } from 'react'
import { isDeviceLandscape, isPhoneScreen, PHONE_LANDSCAPE_VIEWPORT, ROTATE_QUERY, showsRotateScreen } from '../lib/layout'
import { useMediaQuery } from './useMediaQuery'

// The dev phone preview (phone-preview.html) loads its landscape frames with
// `?phone`, because a desktop browser has neither a touch screen nor a
// phone-sized screen. Dev server only; production ignores it.
const PREVIEW = import.meta.env.DEV && new URLSearchParams(window.location.search).has('phone')

type Device = { landscape: boolean | null; phoneScreen: boolean }

function readDevice(): Device {
  const legacy = (window as Window & { orientation?: unknown }).orientation
  return {
    landscape: isDeviceLandscape({
      orientationType: window.screen.orientation?.type,
      windowOrientation: typeof legacy === 'number' ? legacy : null,
    }),
    phoneScreen: isPhoneScreen(window.screen),
  }
}

/**
 * The device's orientation and screen size, re-read on every orientation
 * change and window resize (a foldable unfolding changes its screen size).
 */
function useDevice(): Device {
  const [device, setDevice] = useState(readDevice)
  useEffect(() => {
    const update = () => setDevice((current) => {
      const next = readDevice()
      return next.landscape === current.landscape && next.phoneScreen === current.phoneScreen ? current : next
    })
    const orientation = window.screen.orientation
    orientation?.addEventListener('change', update)
    // iOS before 16.4 has no screen.orientation; it fires orientationchange.
    window.addEventListener('orientationchange', update)
    window.addEventListener('resize', update)
    return () => {
      orientation?.removeEventListener('change', update)
      window.removeEventListener('orientationchange', update)
      window.removeEventListener('resize', update)
    }
  }, [])
  return device
}

/**
 * True while a phone is held sideways: the app shows "turn your phone upright"
 * instead of itself (project/MOBILE_DESIGN.md "Phones are portrait only").
 * Sideways means the device's orientation, never the viewport's shape: an
 * Android keyboard makes an upright phone's viewport wider than tall.
 */
export function useRotateScreen(): boolean {
  const device = useDevice()
  const phoneViewport = useMediaQuery(ROTATE_QUERY)
  const landscapeViewport = useMediaQuery(PHONE_LANDSCAPE_VIEWPORT)
  return showsRotateScreen({
    deviceLandscape: device.landscape,
    phoneViewport,
    landscapeViewport,
    phoneScreen: device.phoneScreen,
    preview: PREVIEW,
  })
}
