import { useEffect, useRef, useState, type MouseEvent, type PointerEvent } from 'react'
import { tooltipShowing } from '../lib/escapeKey'

const LONG_PRESS_MS = 500
const LONG_PRESS_SLOP_PX = 10
const TOOLTIP_AUTO_HIDE_MS = 4000

type Gesture = 'idle' | 'pressing' | 'longpress' | 'cancelled'

/**
 * Touch/pen long-press that opens a details tooltip, shared by the footer's
 * icon controls. One gesture state decides what a click does:
 * idle -> pressing -> longpress | cancelled -> (next pointerdown) idle.
 * A click that ends a long press (or a cancelled gesture) never runs `onTap`,
 * so holding a control shows its details without also triggering it.
 * Keyboard clicks (detail === 0) have no pointer gesture and always run.
 *
 * Mouse users get the CSS hover/focus tooltip, so they never need a hold. A
 * second finger, a cancel, or drifting past the slop cancels the gesture. The
 * tooltip closes after a few seconds, on Escape, or on a press elsewhere.
 * Attach the returned `ref` and handlers to the control; omit `onTap` for a control that only explains.
 */
export function useLongPressTooltip<T extends HTMLElement>(onTap?: () => void) {
  const [open, setOpen] = useState(false)
  const ref = useRef<T>(null)
  const timerRef = useRef<number | null>(null)
  const gestureRef = useRef<Gesture>('idle')
  const pointersRef = useRef(new Set<number>())
  const startRef = useRef<{ x: number; y: number } | null>(null)

  function clearTimer() {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }

  function cancelGesture() {
    clearTimer()
    if (gestureRef.current === 'pressing') gestureRef.current = 'cancelled'
  }

  function onPointerDown(e: PointerEvent) {
    clearTimer()
    if (e.pointerType === 'mouse') {
      pointersRef.current.clear()
      gestureRef.current = 'idle'
      return
    }
    pointersRef.current.add(e.pointerId)
    if (pointersRef.current.size > 1) {
      gestureRef.current = 'cancelled'
      return
    }
    gestureRef.current = 'pressing'
    startRef.current = { x: e.clientX, y: e.clientY }
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      gestureRef.current = 'longpress'
      setOpen(true)
    }, LONG_PRESS_MS)
  }

  function onPointerMove(e: PointerEvent) {
    const start = startRef.current
    if (!start || gestureRef.current !== 'pressing') return
    if (Math.hypot(e.clientX - start.x, e.clientY - start.y) > LONG_PRESS_SLOP_PX) cancelGesture()
  }

  function onPointerUp(e: PointerEvent) {
    pointersRef.current.delete(e.pointerId)
    clearTimer()
  }

  function onPointerCancel(e: PointerEvent) {
    pointersRef.current.delete(e.pointerId)
    cancelGesture()
    gestureRef.current = 'cancelled'
  }

  // Ends the gesture and reports whether this click should act.
  function settleGesture(detail: number): boolean {
    const state = gestureRef.current
    gestureRef.current = 'idle'
    return detail === 0 || (state !== 'longpress' && state !== 'cancelled')
  }

  function onClick(e: MouseEvent) {
    if (!settleGesture(e.detail)) return
    setOpen(false)
    onTap?.()
  }

  useEffect(() => {
    if (!open) return
    const hide = window.setTimeout(() => setOpen(false), TOOLTIP_AUTO_HIDE_MS)
    function onDocPointerDown(e: globalThis.PointerEvent) {
      if (!ref.current?.contains(e.target as Node | null)) setOpen(false)
    }
    document.addEventListener('pointerdown', onDocPointerDown)
    return () => {
      window.clearTimeout(hide)
      document.removeEventListener('pointerdown', onDocPointerDown)
    }
  }, [open])

  // Escape dismisses the tooltip; blurring also drops the CSS focus-visible one.
  // When a tooltip was actually showing, the Escape is used up
  // (preventDefault), so it doesn't also deselect a diagram component or leave
  // the phone Diagram view. Window capture phase, so it runs before those
  // document listeners (lib/escapeKey.ts).
  const openRef = useRef(open)
  useEffect(() => {
    openRef.current = open
  }, [open])
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== 'Escape') return
      if (tooltipShowing(openRef.current, ref.current)) e.preventDefault()
      setOpen(false)
      if (ref.current?.contains(document.activeElement)) (document.activeElement as HTMLElement).blur()
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [])

  useEffect(() => clearTimer, [])

  return {
    open,
    ref,
    onClick,
    onPointerDown,
    onPointerMove,
    onPointerUp,
    onPointerCancel,
    onContextMenu: (e: MouseEvent) => e.preventDefault(),
  }
}
