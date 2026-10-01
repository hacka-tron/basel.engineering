import { useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { nextHeaderFit, shouldShowFullName, type HeaderFit } from '../lib/headerName'

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

/**
 * Below md, which row-one step shows (see nextHeaderFit). `measures` are
 * hidden, always-rendered copies of the full name, short name, Contact label
 * button and Contact icon button, so every step is measured whatever is on
 * screen. `fixed` are the row items that never change (topic menu, GitHub);
 * `rowItems` counts the flex items on the row for the gaps between them.
 */
export function useHeaderFit(
  header: RefObject<HTMLElement | null>,
  measures: { fullName: RefObject<HTMLElement | null>; shortName: RefObject<HTMLElement | null>; contactText: RefObject<HTMLElement | null>; contactIcon: RefObject<HTMLElement | null> },
  fixed: RefObject<HTMLElement | null>[],
  rowItems: number,
  enabled: boolean,
): HeaderFit {
  const [fit, setFit] = useState<HeaderFit>('full')
  const fitRef = useRef<HeaderFit>('full')
  const measuresRef = useRef(measures)
  const fixedRef = useRef(fixed)
  useLayoutEffect(() => { measuresRef.current = measures; fixedRef.current = fixed })

  useLayoutEffect(() => {
    const headerEl = header.current
    if (!headerEl || !enabled) return
    const width = (ref: RefObject<HTMLElement | null>) => ref.current?.getBoundingClientRect().width ?? 0
    const update = () => {
      const style = getComputedStyle(headerEl)
      const content = headerEl.clientWidth - px(style.paddingLeft) - px(style.paddingRight)
      const gap = px(style.columnGap)
      let used = 0
      for (const ref of fixedRef.current) {
        const el = ref.current
        if (!el || el.offsetParent === null) continue
        const s = getComputedStyle(el)
        used += el.getBoundingClientRect().width + px(s.marginLeft) + px(s.marginRight)
      }
      const m = measuresRef.current
      const next = nextHeaderFit(fitRef.current, {
        fullName: width(m.fullName),
        shortName: width(m.shortName),
        contactText: width(m.contactText),
        contactIcon: width(m.contactIcon),
      }, content - used - gap * (rowItems - 1), gap)
      if (next !== fitRef.current) {
        fitRef.current = next
        setFit(next)
      }
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(headerEl)
    for (const ref of [...Object.values(measuresRef.current), ...fixedRef.current]) if (ref.current) observer.observe(ref.current)
    void document.fonts?.ready.then(update)
    document.fonts?.addEventListener?.('loadingdone', update)
    return () => {
      observer.disconnect()
      document.fonts?.removeEventListener?.('loadingdone', update)
    }
  }, [header, rowItems, enabled])
  return fit
}
