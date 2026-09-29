import { useEffect, useState } from 'react'

/**
 * Tracks whether a CSS media query currently matches, updating on viewport
 * changes. Used to keep viewport-specific components (e.g. the desktop
 * architecture diagram) from mounting when they're not visible — CSS-only
 * hiding (`hidden md:block`) still mounts the component, which is wasteful
 * for something as heavy as a React Flow instance.
 */
export function useMediaQuery(query: string): boolean {
  // Lazy initializer covers the value at mount; the effect below only needs
  // to listen for subsequent changes, not to sync it again on mount.
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)

  useEffect(() => {
    const mql = window.matchMedia(query)
    const listener = (event: MediaQueryListEvent) => setMatches(event.matches)

    mql.addEventListener('change', listener)
    return () => mql.removeEventListener('change', listener)
  }, [query])

  return matches
}
