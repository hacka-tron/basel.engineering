import { useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { shouldShowFullName } from '../lib/headerName'

const px = (value: string) => {
  const n = parseFloat(value)
  return Number.isFinite(n) ? n : 0
}

/**
 * True while the full name fits on the header row. Measures the hidden
 * full-name span against the space the Contact/GitHub group leaves; re-runs on
 * any size change of the header or that group, and once fonts load. The
 * group's width and left/right margins plus the row gap are subtracted from
 * the header's content width.
 */
export function useFullNameFits(
  header: RefObject<HTMLElement | null>,
  measure: RefObject<HTMLElement | null>,
  actions: RefObject<HTMLElement | null>,
): boolean {
  const [full, setFull] = useState(true)
  const fullRef = useRef(true)

  useLayoutEffect(() => {
    const headerEl = header.current
    const measureEl = measure.current
    const actionsEl = actions.current
    if (!headerEl || !measureEl) return
    const update = () => {
      const style = getComputedStyle(headerEl)
      const content = headerEl.clientWidth - px(style.paddingLeft) - px(style.paddingRight)
      const gap = px(style.columnGap)
      let available = content
      if (actionsEl && actionsEl.offsetParent !== null) {
        // `ml-auto` computes to the leftover space, so such items are flagged
        // data-auto-margin and counted by width alone.
        const s = getComputedStyle(actionsEl)
        const margins = actionsEl.dataset.autoMargin !== undefined ? 0 : px(s.marginLeft) + px(s.marginRight)
        available -= actionsEl.getBoundingClientRect().width + margins + gap
      }
      const next = shouldShowFullName(fullRef.current, measureEl.getBoundingClientRect().width, available, gap)
      if (next !== fullRef.current) {
        fullRef.current = next
        setFull(next)
      }
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(headerEl)
    observer.observe(measureEl)
    if (actionsEl) observer.observe(actionsEl)
    void document.fonts?.ready.then(update)
    document.fonts?.addEventListener?.('loadingdone', update)
    return () => {
      observer.disconnect()
      document.fonts?.removeEventListener?.('loadingdone', update)
    }
  }, [header, measure, actions])
  return full
}
