// The details sheet's Visuals section (spec 2026-10-02 §5.6): a horizontal
// scroll-snap row at a fixed height whose images keep their declared aspect,
// dots and "1 / N", prev/next on desktop, and a full-screen lightbox.
//
// The lightbox is a native <dialog> opened with showModal(): the rest of the
// page becomes inert and Tab stays inside it (a real focus trap). Escape
// closes only the lightbox: its window capture listener runs before the
// panel's deselect (document capture) and the phone view's back-to-Chat
// (document bubble) and stops the event there. lib/escapeKey.ts has the order.
import { useEffect, useRef, useState } from 'react'
import { galleryControlsShown, lightboxKey, nearestIndex } from '../lib/gallery'
import type { Project } from '../lib/portfolio'

function Arrow({ direction }: { direction: 'left' | 'right' }) {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={direction === 'left' ? 'M10 4l-4 4 4 4' : 'M6 4l4 4-4 4'} />
    </svg>
  )
}

const NAV_BUTTON = 'flex size-11 shrink-0 items-center justify-center rounded-[3px] border border-hairline bg-panel text-muted transition-colors hover:text-primary disabled:opacity-30 disabled:hover:text-muted focus-visible:outline-1 focus-visible:outline-cyan'

function Lightbox({ project, index, onIndex, onClose }: { project: Project; index: number; onIndex: (index: number) => void; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const visual = project.visuals[index]
  const count = project.visuals.length
  const controls = galleryControlsShown(count)

  // Open as a modal; close it however this unmounts (✕, Back leaving the
  // view, a topic switch removing the panel), so the page never stays inert.
  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    dialog.showModal()
    return () => {
      if (dialog.open) dialog.close()
    }
  }, [])

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const step = lightboxKey(event.key, index, count)
      if (!step) return
      event.preventDefault()
      event.stopPropagation()
      if (step.action === 'close') onClose()
      else onIndex(step.index)
    }
    window.addEventListener('keydown', handleKeyDown, true)
    return () => window.removeEventListener('keydown', handleKeyDown, true)
  }, [count, index, onClose, onIndex])

  return (
    <dialog
      ref={dialogRef}
      aria-label={`${project.title} visuals, ${index + 1} of ${count}`}
      // The native cancel (Escape, Android's back gesture) closes it too.
      onCancel={(event) => { event.preventDefault(); onClose() }}
      // A tap on the backdrop area (the dialog itself, not its content) closes.
      onClick={(event) => { if (event.target === event.currentTarget) onClose() }}
      className="m-0 h-dvh max-h-none w-screen max-w-none border-0 bg-black/90 p-4 font-mono text-primary backdrop:bg-transparent [@media(prefers-reduced-transparency:reduce)]:bg-black"
    >
      <div className="pointer-events-none flex h-full flex-col items-center justify-center gap-3">
        <button type="button" aria-label="Close visuals" onClick={onClose} className="pointer-events-auto absolute right-2 top-2 flex size-11 items-center justify-center rounded-[3px] text-xl text-muted transition-colors hover:text-primary focus-visible:outline-1 focus-visible:outline-cyan">✕</button>
        <img
          src={visual.src}
          alt={visual.alt}
          width={visual.width}
          height={visual.height}
          className="pointer-events-auto h-auto max-h-[calc(100dvh-9rem)] w-auto max-w-full rounded-[3px] object-contain"
        />
        <div className="pointer-events-auto flex max-w-full items-center gap-3 text-[13px] leading-[1.6]">
          {controls && <button type="button" aria-label="Previous visual" disabled={index === 0} onClick={() => onIndex(index - 1)} className={NAV_BUTTON}><Arrow direction="left" /></button>}
          <p className="min-w-0 text-center">
            {controls && <span className="text-muted">{index + 1} / {count}</span>}
            {visual.caption && <span className="block break-words">{visual.caption}</span>}
          </p>
          {controls && <button type="button" aria-label="Next visual" disabled={index === count - 1} onClick={() => onIndex(index + 1)} className={NAV_BUTTON}><Arrow direction="right" /></button>}
        </div>
      </div>
    </dialog>
  )
}

export function VisualsGallery({ project }: { project: Project }) {
  const scrollerRef = useRef<HTMLUListElement>(null)
  const openerRef = useRef<HTMLButtonElement | null>(null)
  const [current, setCurrent] = useState(0)
  const [lightbox, setLightbox] = useState<number | null>(null)
  const count = project.visuals.length
  // While prev/next scroll the row, the index they chose stands: when the last
  // visuals already fit, the row can't bring visual 2 to the left edge, and
  // reading the index back from the scroll position would jump from 1 to 3.
  const steppingRef = useRef<number | null>(null)
  useEffect(() => () => { if (steppingRef.current !== null) window.clearTimeout(steppingRef.current) }, [])
  function holdIndex(ms: number) {
    if (steppingRef.current !== null) window.clearTimeout(steppingRef.current)
    steppingRef.current = window.setTimeout(() => { steppingRef.current = null }, ms)
  }

  function handleScroll() {
    const scroller = scrollerRef.current
    if (!scroller) return
    if (steppingRef.current !== null) {
      // Still scrolling from prev/next: wait until the row settles.
      holdIndex(150)
      return
    }
    const lefts = [...scroller.children].map((item) => (item as HTMLElement).offsetLeft - scroller.offsetLeft)
    const atEnd = scroller.scrollLeft + scroller.clientWidth >= scroller.scrollWidth - 2
    setCurrent(nearestIndex(lefts, scroller.scrollLeft, atEnd))
  }

  function scrollToIndex(index: number) {
    const scroller = scrollerRef.current
    const item = scroller?.children[index] as HTMLElement | undefined
    if (!scroller || !item) return
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    holdIndex(400)
    scroller.scrollTo({ left: item.offsetLeft - scroller.offsetLeft, behavior: reduceMotion ? 'auto' : 'smooth' })
    setCurrent(index)
  }

  function closeLightbox() {
    setLightbox(null)
    // Back to the thumbnail that opened it, after the dialog has gone.
    requestAnimationFrame(() => openerRef.current?.focus())
  }

  return (
    <div>
      <ul ref={scrollerRef} onScroll={handleScroll} aria-label={`${project.title} screenshots`} className="-mx-5 flex snap-x snap-mandatory gap-3 overflow-x-auto scroll-px-5 px-5 pb-2 [scrollbar-width:thin] md:-mx-8 md:scroll-px-8 md:px-8">
        {project.visuals.map((visual, index) => (
          <li key={`${index}:${visual.src}`} className="shrink-0 snap-start">
            <figure className="m-0 w-min">
              <button
                type="button"
                onClick={(event) => { openerRef.current = event.currentTarget; setLightbox(index) }}
                aria-label={`Open ${visual.alt} full screen`}
                className="block rounded-[3px] transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan"
              >
                {/* Fixed height, width from the declared aspect: a 9:19.5 phone shot sits beside 16:10 ones, and nothing shifts as images load. */}
                <img src={visual.src} alt="" width={visual.width} height={visual.height} loading="lazy" decoding="async" style={{ aspectRatio: `${visual.width} / ${visual.height}` }} className="block h-[clamp(9rem,42vw,15rem)] w-auto max-w-none rounded-[3px] border border-hairline bg-canvas object-cover" />
              </button>
              {visual.caption && <figcaption className="mt-1.5 break-words text-xs leading-snug text-muted">{visual.caption}</figcaption>}
            </figure>
          </li>
        ))}
      </ul>
      {galleryControlsShown(count) && (
        <div className="mt-1 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2" aria-hidden="true">
            {project.visuals.map((visual, index) => <span key={`${index}:${visual.src}`} className={`size-1.5 rounded-full ${index === current ? 'bg-cyan' : 'bg-hairline'}`} />)}
            <span className="ml-1 text-xs text-muted">{current + 1} / {count}</span>
          </div>
          {/* Phones swipe; desktop gets buttons. */}
          <div className="hidden gap-2 md:flex">
            <button type="button" aria-label="Previous screenshot" disabled={current === 0} onClick={() => scrollToIndex(current - 1)} className={NAV_BUTTON}><Arrow direction="left" /></button>
            <button type="button" aria-label="Next screenshot" disabled={current === count - 1} onClick={() => scrollToIndex(current + 1)} className={NAV_BUTTON}><Arrow direction="right" /></button>
          </div>
        </div>
      )}
      {lightbox !== null && <Lightbox project={project} index={lightbox} onIndex={setLightbox} onClose={closeLightbox} />}
    </div>
  )
}
