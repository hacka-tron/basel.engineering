/**
 * Keeping the architecture diagram fitted to its container.
 *
 * React Flow's `fitView` prop only runs once, when the nodes first measure.
 * After that a resize (dragging the window, the portrait <-> desktop switch,
 * the details panel opening) leaves the old zoom behind, so a diagram fitted
 * while the window was narrow stayed small after it was widened again. The
 * panel now asks for a refit whenever its container changes size; the fit is
 * set directly on the viewport (not via `fitView`, which React Flow defers
 * while node data is changing) and is never tied to trace updates.
 */
export type Size = { width: number; height: number }
export type Bounds = { x: number; y: number; width: number; height: number }

/** Bounding box of fixed-size nodes. */
export function boundsOf(positions: { x: number; y: number }[], nodeWidth: number, nodeHeight: number): Bounds {
  const xs = positions.map((p) => p.x)
  const ys = positions.map((p) => p.y)
  const x = Math.min(...xs)
  const y = Math.min(...ys)
  return { x, y, width: Math.max(...xs) - x + nodeWidth, height: Math.max(...ys) - y + nodeHeight }
}

type Frames = {
  request: (callback: () => void) => number
  cancel: (handle: number) => void
}

/**
 * Coalesces resize notifications into one fit per frame. Sizes of zero (the
 * container is hidden or not laid out yet) are skipped so a later, real
 * measurement still gets its fit.
 */
export function createRefitter(measure: () => Size, fit: (size: Size) => void, frames: Frames) {
  let handle: number | null = null
  return {
    request() {
      if (handle !== null) frames.cancel(handle)
      handle = frames.request(() => {
        handle = null
        const size = measure()
        if (size.width > 0 && size.height > 0) fit(size)
      })
    },
    cancel() {
      if (handle !== null) frames.cancel(handle)
      handle = null
    },
  }
}
