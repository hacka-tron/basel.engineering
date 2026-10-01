import { useLayoutEffect, useRef, type KeyboardEvent } from 'react'

export type TopicChip<T extends string> = { value: T; label: string; short: string }

type TopicChipsProps<T extends string> = {
  value: T
  options: TopicChip<T>[]
  onChange: (value: T) => void
  /** Called if a chip still has focus when the chips unmount (e.g. on a switch to Diagram view). */
  onUnmountWithFocus?: () => void
}

/**
 * The question topic as a radio group directly above the ask box, the only
 * topic control at every width: "Asking about (● Basel) (○ System)". Below md
 * it is one 44px row in Chat view only (the Diagram view unmounts it and gives
 * the diagram that height; the topic is unchanged, so a question typed there
 * still uses it). At md+ it is always shown, a compact row with 12px pills
 * like the rest of the desktop's secondary UI. Roving
 * tabindex: Tab reaches the checked chip; arrows (and Home/End) move and
 * select, as in a native radio group. Accessible names are the full topic
 * names; the visible short labels are contained in them.
 */
function TopicChips<T extends string>({ value, options, onChange, onUnmountWithFocus }: TopicChipsProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const rootRef = useRef<HTMLDivElement>(null)
  const onUnmountRef = useRef(onUnmountWithFocus)
  useLayoutEffect(() => { onUnmountRef.current = onUnmountWithFocus })
  // Layout-effect cleanup runs before React removes the DOM, so the focused
  // chip can still be detected; the callback moves focus once it is gone.
  useLayoutEffect(() => {
    const root = rootRef.current
    return () => {
      if (root?.contains(document.activeElement)) {
        const rescue = onUnmountRef.current
        if (rescue) queueMicrotask(rescue)
      }
    }
  }, [])

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const index = options.findIndex((option) => option.value === value)
    const last = options.length - 1
    const to = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? (index === last ? 0 : index + 1)
      : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? (index <= 0 ? last : index - 1)
        : event.key === 'Home' ? 0
          : event.key === 'End' ? last
            : null
    if (to === null) return
    event.preventDefault()
    onChange(options[to].value)
    refs.current[to]?.focus()
  }

  return (
    <div ref={rootRef} className="flex min-h-11 items-center gap-1 md:mb-3 md:min-h-0">
      {/* The label becomes screen-reader-only where it would push the System chip past the column: below 320px (e.g. a 280px Fold cover screen) and in the narrow desktop chat column at 768-799px. The radiogroup keeps its name. */}
      <span id="topic-chips-label" className="mr-1 whitespace-nowrap text-xs text-muted max-[319px]:sr-only min-[768px]:max-[799px]:sr-only">Asking about</span>
      <div role="radiogroup" aria-labelledby="topic-chips-label" onKeyDown={onKeyDown} className="flex items-center">
        {options.map((option, index) => {
          const checked = option.value === value
          return (
            <button
              key={option.value}
              ref={(el) => { refs.current[index] = el }}
              type="button"
              role="radio"
              aria-checked={checked}
              aria-label={option.label}
              tabIndex={checked ? 0 : -1}
              onClick={() => { if (!checked) onChange(option.value) }}
              className="group flex min-h-11 items-center px-1 outline-none md:min-h-0 md:py-0.5"
            >
              <span
                className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-sm leading-tight transition-colors md:text-xs group-focus-visible:outline-2 group-focus-visible:outline-offset-2 group-focus-visible:outline-cyan ${
                  checked ? 'border-cyan text-cyan' : 'border-hairline text-muted group-hover:text-primary'
                }`}
              >
                <span aria-hidden="true" className={`size-2 shrink-0 rounded-full border ${checked ? 'border-cyan bg-cyan' : 'border-muted'}`} />
                {option.short}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export default TopicChips
