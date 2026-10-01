import { useRef, type KeyboardEvent } from 'react'

export type TopicChip<T extends string> = { value: T; label: string; short: string }

type TopicChipsProps<T extends string> = {
  value: T
  options: TopicChip<T>[]
  onChange: (value: T) => void
}

/**
 * Below md, the question topic as a radio group directly above the ask box:
 * "Asking about (● Basel) (○ System)". One 44px row in both Chat and Diagram
 * views, so the topic is always next to where the question is typed. Roving
 * tabindex: Tab reaches the checked chip; arrows (and Home/End) move and
 * select, as in a native radio group. Accessible names are the full topic
 * names; the visible short labels are contained in them.
 */
function TopicChips<T extends string>({ value, options, onChange }: TopicChipsProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])

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
    <div className="flex min-h-11 items-center gap-1 md:hidden">
      <span id="topic-chips-label" className="mr-1 whitespace-nowrap text-xs text-muted">Asking about</span>
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
              className="group flex min-h-11 items-center px-1 outline-none"
            >
              <span
                className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-sm leading-tight transition-colors group-focus-visible:outline-2 group-focus-visible:outline-offset-2 group-focus-visible:outline-cyan ${
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
