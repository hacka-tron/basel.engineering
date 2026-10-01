/**
 * How the mobile footer fits "New chat" beside the stats.
 *
 * The stats (first token, cache marker, query count) always keep one line and
 * are never wrapped or shrunk. The New chat control gets whatever is left
 * between them and the stress-test control, with breathing room on both
 * sides:
 * - `label`: the labelled "New chat" button fits.
 * - `compact`: only the square "+" button fits.
 * - `tight`: not even "+" fits next to the full stats (a narrow phone with
 *   long numbers), so the timing drops its "first token" prefix to make room
 *   (the full wording stays available to screen readers and as a tooltip).
 * Decided from measured widths rather than a breakpoint, because the stats
 * grow once real numbers arrive.
 */
export const NEW_CHAT_GAP_PX = 12

export type FooterWidths = {
  /** Footer content width (padding excluded). */
  available: number
  /** The stats at full length (including the "first token" prefix). */
  stats: number
  /** Width of the "first token " prefix (0 when the timing has none). */
  prefix: number
  stressControl: number
  /** Labelled button width. */
  label: number
  /** Square "+" button width. */
  compact: number
}

export type NewChatFit = 'label' | 'compact' | 'tight'

export function newChatFit({ available, stats, prefix, stressControl, label, compact }: FooterWidths): NewChatFit {
  const room = available - stressControl - NEW_CHAT_GAP_PX * 2
  if (room - stats >= label) return 'label'
  if (room - stats >= compact || prefix === 0) return 'compact'
  return 'tight'
}
