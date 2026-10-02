// The Visuals gallery and lightbox (spec 2026-10-02 §5.6).

/** Index of the item whose left offset is nearest `scrollLeft`; the last one when scrolled to the end. */
export function nearestIndex(itemLefts: readonly number[], scrollLeft: number, atEnd: boolean): number {
  if (itemLefts.length === 0) return 0
  if (atEnd) return itemLefts.length - 1
  let best = 0
  itemLefts.forEach((left, index) => {
    if (Math.abs(left - scrollLeft) < Math.abs(itemLefts[best] - scrollLeft)) best = index
  })
  return best
}

export type LightboxStep = { action: 'close' } | { action: 'show'; index: number }

/** Escape closes only the lightbox; ArrowLeft/ArrowRight move within it and stop at the ends. */
export function lightboxKey(key: string, index: number, count: number): LightboxStep | null {
  if (key === 'Escape') return { action: 'close' }
  if (key === 'ArrowRight' && index < count - 1) return { action: 'show', index: index + 1 }
  if (key === 'ArrowLeft' && index > 0) return { action: 'show', index: index - 1 }
  return null
}

export function galleryControlsShown(count: number): boolean {
  return count > 1
}
