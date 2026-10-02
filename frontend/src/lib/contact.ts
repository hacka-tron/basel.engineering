// The contact address and how it is copied. Shared by the header envelope
// (ContactReveal) and the footer "Open to work" popover.
//
// The address lives in the JS bundle only (never in the static HTML), so a
// naive scraper of index.html doesn't see it. contact.test.ts checks that.
export const EMAIL = 'baselmabdelrahman@gmail.com'

export const COPIED_TEXT = 'Email copied'

export type CopyDeps = {
  /** The async Clipboard API, or undefined where it is missing (insecure context, old browser). */
  writeText?: (text: string) => Promise<void>
  /** The execCommand('copy') fallback; true when it copied. */
  legacyCopy: (text: string) => boolean
}

/**
 * Copies `text`: the Clipboard API first, the execCommand fallback second.
 * Resolves true when either worked. Never rejects: a fallback that throws
 * counts as a failure, so the caller can show the address instead.
 */
export async function copyText(text: string, deps: CopyDeps): Promise<boolean> {
  if (deps.writeText) {
    try {
      await deps.writeText(text)
      return true
    } catch {
      // Denied or unavailable: try the fallback.
    }
  }
  try {
    return deps.legacyCopy(text)
  } catch {
    return false
  }
}

/** What the copy button shows afterwards, and for how long. */
export function copyFeedback(ok: boolean): { text: string; ms: number } {
  return ok ? { text: COPIED_TEXT, ms: 2000 } : { text: EMAIL, ms: 5000 }
}

/** Legacy copy path for when the async Clipboard API is missing or denied. */
function legacyCopy(text: string): boolean {
  const previouslyFocused = document.activeElement as HTMLElement | null
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.top = '0'
  textarea.style.left = '0'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  textarea.setSelectionRange(0, text.length)
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    document.body.removeChild(textarea)
    // Selecting the textarea stole focus; hand it back (keyboard users).
    previouslyFocused?.focus?.()
  }
}

/** Copies the address in the browser. Resolves false when nothing worked. */
export function copyEmail(): Promise<boolean> {
  const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard
  return copyText(EMAIL, {
    writeText: clipboard ? (text) => clipboard.writeText(text) : undefined,
    legacyCopy,
  })
}
