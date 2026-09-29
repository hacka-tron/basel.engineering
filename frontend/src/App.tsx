import { useState } from 'react'
import Chat from './components/Chat'
import ArchitecturePanel from './components/ArchitecturePanel'
import StatsBar from './components/StatsBar'

export type Corpus = 'basel' | 'system'

function App() {
  const [corpus, setCorpus] = useState<Corpus>('basel')

  return (
    <div className="flex h-screen min-h-0 flex-col overflow-hidden bg-canvas font-mono text-primary">
      <header className="flex h-[72px] shrink-0 items-center border-b border-hairline px-8">
        <h1 className="text-base font-semibold tracking-tight">Basel Abdel-Rahman</h1>

        <nav aria-label="Question topic" className="ml-auto flex items-center gap-2 text-xs">
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
          className="ml-12 text-xs text-muted transition-colors hover:text-primary"
        >
          GitHub ↗
        </a>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-[40%_60%]">
        <Chat corpus={corpus} />
        <ArchitecturePanel />
      </main>

      <StatsBar />
    </div>
  )
}

export default App
