/**
 * Full name vs short name in the header.
 *
 * The header row is [name, (desktop: topic nav), Contact me + GitHub]. The
 * full name is shown whenever its natural width fits in what the other items
 * leave; otherwise the short form keeps Contact and GitHub on the first line.
 * Decided by measurement (fonts and the "Email copied" label change widths),
 * with a small hysteresis so a width sitting right on the threshold cannot
 * flip back and forth.
 */
export const FULL_NAME = 'Basel Abdel-Rahman'
export const SHORT_NAME = 'Basel A-R'

/**
 * Minimum breathing room between the end of the name and whatever follows it
 * (Contact me, or the topic nav at md+). Below md the row's own gap is
 * already 16px, so only the shortfall against this value is reserved.
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

/**
 * Mobile row-one fit, in priority order: full name with the "Contact me"
 * label, then the short name with the label, then the short name with
 * Contact as an envelope icon. Same minimum gap and hysteresis as above:
 * a step that gives room back (a lower index than the current one) needs
 * RETURN_MARGIN_PX extra before it returns.
 */
export const HEADER_FITS = ['full', 'short', 'icon'] as const
export type HeaderFit = (typeof HEADER_FITS)[number]

export type HeaderFitWidths = {
  fullName: number
  shortName: number
  contactText: number
  contactIcon: number
}

/**
 * @param availableWidth width left for the name plus the Contact control
 *   (content width minus the fixed row items and the gaps between items)
 */
export function nextHeaderFit(current: HeaderFit, widths: HeaderFitWidths, availableWidth: number, rowGap = 0): HeaderFit {
  const { fullName, shortName, contactText, contactIcon } = widths
  if (fullName <= 0 || shortName <= 0 || contactText <= 0 || contactIcon <= 0 || availableWidth <= 0) return current
  const available = availableWidth - Math.max(0, MIN_GAP_PX - rowGap)
  const need: Record<HeaderFit, number> = {
    full: fullName + contactText,
    short: shortName + contactText,
    icon: shortName + contactIcon,
  }
  const currentIndex = HEADER_FITS.indexOf(current)
  for (const [index, fit] of HEADER_FITS.entries()) {
    if (fit === 'icon') return fit
    const margin = index < currentIndex ? RETURN_MARGIN_PX : 0
    if (need[fit] + margin <= available) return fit
  }
  return 'icon'
}
