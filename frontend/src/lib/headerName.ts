/**
 * Full name vs short name in the header.
 *
 * The header row is [name ... Contact envelope + GitHub] at every width. The
 * full name is shown whenever its natural width fits in what the other items
 * leave; otherwise the short form keeps Contact and GitHub on the first line.
 * Decided by measurement (font loading changes widths),
 * with a small hysteresis so a width sitting right on the threshold cannot
 * flip back and forth.
 */
export const FULL_NAME = 'Basel Abdel-Rahman'
export const SHORT_NAME = 'Basel A-R'

/**
 * Minimum breathing room between the end of the name and the Contact envelope.
 * The row's own gap is already 16px, so only the shortfall against this value
 * is reserved.
 */
export const MIN_GAP_PX = 16

/** Extra room needed before the full name returns after the short one. */
export const RETURN_MARGIN_PX = 8

/**
 * @param showingFull what is on screen now
 * @param fullWidth natural width of the full name
 * @param availableWidth width the name's slot may use (container content width
 *   minus the other row items and the gaps between them)
 * @param rowGap the row's column gap, already part of the layout
 */
export function shouldShowFullName(showingFull: boolean, fullWidth: number, availableWidth: number, rowGap = 0): boolean {
  if (fullWidth <= 0 || availableWidth <= 0) return showingFull
  const available = availableWidth - Math.max(0, MIN_GAP_PX - rowGap)
  return showingFull ? fullWidth <= available : fullWidth + RETURN_MARGIN_PX <= available
}
