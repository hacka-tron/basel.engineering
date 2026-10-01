import { useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { shouldShowFullName } from '../lib/headerName'

const px = (value: string) => {
  const n = parseFloat(value)
  return Number.isFinite(n) ? n : 0
}

/**
 * True while the full name fits on the header's first row. Measures the
 * hidden full-name span against the space the other row items leave; re-runs
 * on any size change of the header or those items, and once fonts load.
 * `others` are the visible siblings of the name (Contact/GitHub group, and the
 * topic nav at md+); their widths and left/right margins plus the row gap are
 * subtracted from the header's content width.
 */
export function useFullNameFits(
  header: RefObject<HTMLElement | null>,
  measure: RefObject<HTMLElement | null>,
  others: RefObject<HTMLElement | null>[],
): boolean {
  const [full, setFull] = useState(true)
  const fullRef = useRef(true)
  const othersRef = useRef(others)
  useLayoutEffect(() => { othersRef.current = others })

  useLayoutEffect(() => {
    const headerEl = header.current
    const measureEl = measure.current
    if (!headerEl || !measureEl) return
    const update = () => {
      const style = getComputedStyle(headerEl)
      const content = headerEl.clientWidth - px(style.paddingLeft) - px(style.paddingRight)
      const gap = px(style.columnGap)
      let used = 0
      let count = 1 // the name itself
      for (const ref of othersRef.current) {
        const el = ref.current
        if (!el || el.offsetParent === null) continue
        // `ml-auto` computes to the leftover space, so such items are flagged
        // data-auto-margin and counted by width alone.
        const s = getComputedStyle(el)
        const margins = el.dataset.autoMargin !== undefined ? 0 : px(s.marginLeft) + px(s.marginRight)
        used += el.getBoundingClientRect().width + margins
        count += 1
      }
      // Row-one gaps: between name and each other item. Below md the nav is
      // on row two (full width) and is excluded by the wrap, not measured.
      const available = content - used - gap * (count - 1)
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
    for (const ref of othersRef.current) if (ref.current) observer.observe(ref.current)
    void document.fonts?.ready.then(update)
    document.fonts?.addEventListener?.('loadingdone', update)
    return () => {
      observer.disconnect()
      document.fonts?.removeEventListener?.('loadingdone', update)
    }
  }, [header, measure, others.length])
  return full
}
