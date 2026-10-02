// When a small non-modal footer popover (the "Open to work" item) closes.
// `item` is the element wrapping both the trigger and the popover. The
// trigger's own click toggles it; these rules cover everything else.

export type ContainerLike = { contains: (node: never) => boolean } | null

function inside(node: unknown, item: ContainerLike): boolean {
  return item !== null && node !== null && node !== undefined && (item.contains as (node: unknown) => boolean)(node)
}

/** A pointerdown outside the trigger and the popover closes it. */
export function closesOnPointerDown(target: unknown, item: ContainerLike): boolean {
  return !inside(target, item)
}

/**
 * Focus moving to another element outside the item closes it (Tab past the
 * popover; on phones, the ask box taking focus starts focus mode, which
 * slides the footer away). Focus going nowhere (`relatedTarget` null: a
 * click on the popover's plain text, or the window losing focus) keeps it
 * open; an outside tap is handled by `closesOnPointerDown`.
 */
export function closesOnFocusOut(next: unknown, item: ContainerLike): boolean {
  return next !== null && next !== undefined && !inside(next, item)
}

/**
 * Escape closes the open popover unless an earlier handler used it (a
 * showing footer tooltip, see lib/escapeKey.ts) or it ends an IME
 * composition (Safari reports that as keyCode 229 rather than isComposing).
 */
export function takesEscape(event: { key: string; defaultPrevented: boolean; isComposing?: boolean; keyCode?: number }): boolean {
  return event.key === 'Escape' && !event.defaultPrevented && !event.isComposing && event.keyCode !== 229
}
