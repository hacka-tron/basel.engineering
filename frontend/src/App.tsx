import { useEffect, useRef, useState } from 'react'
import Chat from './components/Chat'
import ArchitecturePanel from './components/ArchitecturePanel'
import PipelineStrip from './components/PipelineStrip'
import StatsBar from './components/StatsBar'
import { useMediaQuery } from './hooks/useMediaQuery'

export type Corpus = 'basel' | 'system'

function App() {
  const [corpus, setCorpus] = useState<Corpus>('basel')
  const [architectureOpen, setArchitectureOpen] = useState(false)
  // Matches Tailwind's `md` breakpoint. Drives which ArchitecturePanel /
  // React Flow instance is mounted so only one ever exists at a time — see
  // the comment above the desktop panel render below.
  const isDesktop = useMediaQuery('(min-width: 768px)')
  // Derived rather than synced via an effect: even if `architectureOpen`
  // stays true while the viewport crosses into desktop width (e.g. rotating
  // a tablet), the sheet — and its ArchitecturePanel/React Flow instance —
  // simply never renders at md+, so it can never coexist with the desktop
  // panel below.
  const showArchitectureSheet = architectureOpen && !isDesktop

  // Focus management for the mobile bottom sheet: it declares
  // `aria-modal="true"`, which is a promise to assistive tech that focus is
  // contained. A full Tab-cycling focus trap is out of scope for this
  // skeleton, but Escape-to-close, initial focus into the dialog, and focus
  // restoration on close are the minimum needed to honor that promise.
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const triggerButtonRef = useRef<HTMLButtonElement | null>(null)

  const closeArchitectureSheet = () => {
    setArchitectureOpen(false)
    triggerButtonRef.current?.focus()
  }

  useEffect(() => {
    if (!showArchitectureSheet) return

    closeButtonRef.current?.focus()

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setArchitectureOpen(false)
        triggerButtonRef.current?.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [showArchitectureSheet])

  return (
    <div className="flex h-screen min-h-0 flex-col overflow-hidden bg-canvas font-mono text-primary">
      <header className="flex min-h-[72px] shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-hairline px-4 py-3 md:h-[72px] md:flex-nowrap md:gap-0 md:px-8 md:py-0">
        <h1 className="w-full text-base font-semibold tracking-tight md:w-auto">Basel Abdel-Rahman</h1>

        <a
          href="mailto:baselmabdelrahman@gmail.com"
          className="text-xs text-muted transition-colors hover:text-primary md:ml-4"
        >
          Contact me
        </a>

        <nav aria-label="Question topic" className="flex items-center gap-2 text-xs md:ml-auto">
          <button
            type="button"
            aria-pressed={corpus === 'basel'}
            onClick={() => setCorpus('basel')}
            className={`rounded-[3px] px-3 py-2 transition-colors hover:text-primary ${corpus === 'basel' ? 'text-cyan' : 'text-muted'}`}
          >
            About Basel
          </button>
          <span aria-hidden="true" className="text-hairline">|</span>
          <button
            type="button"
            aria-pressed={corpus === 'system'}
            onClick={() => setCorpus('system')}
            className={`rounded-[3px] px-3 py-2 transition-colors hover:text-primary ${corpus === 'system' ? 'text-cyan' : 'text-muted'}`}
          >
            About This System
          </button>
        </nav>

        <a
          href="https://github.com/hacka-tron"
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto text-xs text-muted transition-colors hover:text-primary md:ml-12"
        >
          GitHub ↗
        </a>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[40%_60%]">
        <Chat
          corpus={corpus}
          inputAccessory={
            <PipelineStrip
              triggerRef={triggerButtonRef}
              onViewArchitecture={() => setArchitectureOpen(true)}
            />
          }
        />
        {/*
          Mounted only at md+ instead of CSS-hidden below it: React Flow sets
          up a ResizeObserver, zoom/pan handlers, and internal store on mount,
          so leaving it always-mounted-but-hidden on mobile wastes work and
          risks a second instance rendering into a zero-size container
          whenever the sheet below is also open. Only one ArchitecturePanel
          is ever mounted at a time (this one, or the sheet's).
        */}
        {isDesktop && <ArchitecturePanel />}
      </main>

      <StatsBar />

      {showArchitectureSheet && (
        <div role="dialog" aria-modal="true" aria-label="Architecture" className="fixed inset-0 z-50 md:hidden">
          <button
            type="button"
            aria-label="Close architecture panel"
            onClick={closeArchitectureSheet}
            className="absolute inset-0 bg-black/70"
          />
          <div className="absolute inset-x-0 bottom-0 flex h-[80dvh] flex-col border-t border-hairline bg-panel">
            <div className="flex h-12 shrink-0 items-center justify-between border-b border-hairline px-4">
              <h2 className="text-sm text-primary">Architecture</h2>
              <button
                ref={closeButtonRef}
                type="button"
                aria-label="Close architecture panel"
                onClick={closeArchitectureSheet}
                className="px-2 text-xl text-muted hover:text-primary"
              >
                ×
              </button>
            </div>
            <div className="flex min-h-0 flex-1 flex-col [&>section]:flex-1">
              <ArchitecturePanel />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
