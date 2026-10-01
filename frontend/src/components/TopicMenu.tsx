import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'

export type TopicOption<T extends string> = { value: T; label: string; short: string }

type TopicMenuProps<T extends string> = {
  value: T
  options: TopicOption<T>[]
  onChange: (value: T) => void
  className?: string
}

const VIEWPORT_MARGIN_PX = 8

/**
 * Below md, the question topic as a menu button in the header row: the
 * button shows the current topic, the menu lists both with a check on the
 * current one (menuitemradio). Escape and a tap outside close it; Escape and
 * a selection return focus to the button; arrows, Home and End move.
 */
function TopicMenu<T extends string>({ value, options, onChange, className = '' }: TopicMenuProps<T>) {
  const [open, setOpen] = useState(false)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([])
  const menuId = useId()
  const current = options.find((option) => option.value === value) ?? options[0]

  // Centred under the button, nudged to stay 8px inside the viewport.
  useLayoutEffect(() => {
    const menu = menuRef.current
    if (!open || !menu) return
    menu.style.transform = 'translateX(-50%)'
    const { left, right } = menu.getBoundingClientRect()
    const max = document.documentElement.clientWidth - VIEWPORT_MARGIN_PX
    const shift = left < VIEWPORT_MARGIN_PX ? VIEWPORT_MARGIN_PX - left : right > max ? max - right : 0
    menu.style.transform = `translateX(calc(-50% + ${shift}px))`
  }, [open])

  useEffect(() => {
    if (!open) return
    const index = Math.max(0, options.findIndex((option) => option.value === value))
    itemRefs.current[index]?.focus()
    const onPointerDown = (event: PointerEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onFocusIn = (event: FocusEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('focusin', onFocusIn)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('focusin', onFocusIn)
    }
  }, [open, options, value])

  function close() {
    setOpen(false)
    buttonRef.current?.focus()
  }

  function onMenuKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const items = itemRefs.current.filter((item): item is HTMLButtonElement => item !== null)
    const index = items.indexOf(document.activeElement as HTMLButtonElement)
    const move = (to: number) => { event.preventDefault(); items[(to + items.length) % items.length]?.focus() }
    if (event.key === 'ArrowDown') move(index + 1)
    else if (event.key === 'ArrowUp') move(index - 1)
    else if (event.key === 'Home') move(0)
    else if (event.key === 'End') move(items.length - 1)
    else if (event.key === 'Escape') { event.preventDefault(); close() }
    else if (event.key === 'Tab') setOpen(false)
  }

  return (
    <div
      ref={wrapRef}
      className={`relative shrink-0 ${className}`}
    >
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Question topic: ${current.label}`}
        onClick={() => setOpen((was) => !was)}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setOpen(true) }
        }}
        className="inline-flex min-h-11 items-center gap-1 whitespace-nowrap rounded-[3px] px-2 text-sm text-cyan transition-colors hover:text-primary"
      >
        <span aria-hidden="true">{current.short}</span>
        <svg viewBox="0 0 16 16" className={`size-4 transition-transform duration-200 ease-out motion-reduce:transition-none ${open ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m4 6 4 4 4-4" />
        </svg>
      </button>
      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label="Question topic"
          onKeyDown={onMenuKeyDown}
          style={{ transform: 'translateX(-50%)' }}
          className="absolute left-1/2 top-full z-30 mt-1 flex min-w-max flex-col rounded-[3px] border border-hairline bg-panel py-1 shadow-lg"
        >
          {options.map((option, index) => {
            const checked = option.value === value
            return (
              <button
                key={option.value}
                ref={(el) => { itemRefs.current[index] = el }}
                type="button"
                role="menuitemradio"
                aria-checked={checked}
                tabIndex={-1}
                onClick={() => { if (!checked) onChange(option.value); close() }}
                className={`flex min-h-11 items-center gap-2 px-3 text-left text-sm outline-none transition-colors hover:bg-canvas focus-visible:bg-canvas ${checked ? 'text-cyan' : 'text-primary'}`}
              >
                <svg viewBox="0 0 16 16" className={`size-4 shrink-0 ${checked ? '' : 'invisible'}`} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="m3.5 8.5 3 3 6-7" />
                </svg>
                {option.label}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default TopicMenu
