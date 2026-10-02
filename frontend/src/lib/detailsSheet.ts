// The shared pull-up details sheet (spec 2026-10-02 §5.5): it covers the
// bottom 80% of its region, so a strip of the grid or diagram stays visible
// above it. These helpers place the selected item in that strip. The sheet's
// `top-[20%]` class in components/DetailsSheet.tsx must match SHEET_COVER.

export const SHEET_COVER = 0.8

/** The phone header's fade band (`.header-fade`) overlaps the region's top 16px. */
export const HEADER_FADE_PX = 16

export type Viewport = { x: number; y: number; zoom: number }

/** Vertical centre, in px from the region's top, of the strip the sheet leaves uncovered. */
export function stripCenterY(regionHeight: number): number {
  return (regionHeight * (1 - SHEET_COVER)) / 2 + HEADER_FADE_PX / 2
}

/**
 * The fitted diagram viewport, panned vertically at the same zoom and x so a
 * node whose centre is at `nodeCenterY` (flow coordinates) sits in the middle
 * of the strip. Deselecting returns to the plain fit.
 */
export function panIntoStrip(fit: Viewport, nodeCenterY: number, regionHeight: number): Viewport {
  return { x: fit.x, y: stripCenterY(regionHeight) - nodeCenterY * fit.zoom, zoom: fit.zoom }
}

/** The list scrollTop that puts an item `gap` px below the list's top edge (never negative). */
export function scrollTopForItem(scrollTop: number, listTop: number, itemTop: number, gap = 12): number {
  return Math.max(0, scrollTop + itemTop - listTop - gap)
}
