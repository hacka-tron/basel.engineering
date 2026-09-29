import { useState, type FormEvent } from 'react'
import type { Corpus } from '../App'

const questions: Record<Corpus, string[]> = {
  basel: [
    'What has Basel built with distributed systems?',
    'What did Basel work on at YouTube?',
    'Is Basel a fit for a platform engineering role?',
  ],
  system: [
    'How does the caching work?',
    'Why k3s instead of EKS?',
    'What happens when I press stress test?',
    'Show me the Terraform for the database.',
  ],
}

type ChatProps = {
  corpus: Corpus
}

function Chat({ corpus }: ChatProps) {
  const [question, setQuestion] = useState('')

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    console.log(question)
  }

  return (
    <section aria-label="Chat" className="flex min-h-0 flex-col border-r border-hairline bg-panel">
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <div aria-label="Messages" className="min-h-0 flex-1" />

        <div className="shrink-0 px-7 pb-6">
          <p className="mb-3 text-xs text-muted">Suggested questions</p>
          <div className="flex flex-wrap gap-2">
            {questions[corpus].map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => setQuestion(suggestion)}
                className="rounded-[3px] border border-hairline px-3 py-2 text-left text-xs leading-relaxed text-muted transition-colors hover:text-primary"
              >
                {suggestion}
              </button>
            ))}
          </div>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="flex shrink-0 gap-2 px-7 pb-7">
        <input
          aria-label="Ask anything"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask anything..."
          className="min-w-0 flex-1 rounded-[3px] border border-hairline bg-canvas px-3 py-3 text-sm text-primary outline-none placeholder:text-muted focus:border-cyan"
        />
        <button
          type="submit"
          aria-label="Send question"
          className="rounded-[3px] border border-hairline bg-canvas px-4 text-lg text-muted transition-colors hover:text-primary"
        >
          →
        </button>
      </form>
    </section>
  )
}

export default Chat
