import type { ReactNode } from 'react'
import type { Kind, Project } from '../lib/portfolio'
import { questionForProject } from '../lib/portfolioView'
import { ContinueInChat, SheetSection, SHEET_BODY, SHEET_TITLE } from './DetailsSheet'
import MarkdownBody from './MarkdownBody'

function KindBadge({ kind }: { kind: Kind }) {
  return (
    <span className={`shrink-0 rounded-[2px] border px-1.5 text-[11px] leading-[1.6] ${kind === 'freelance' ? 'border-hit/50 text-hit' : 'border-hairline text-muted'}`}>
      {kind}
    </span>
  )
}

const LINK_BUTTON = 'inline-flex min-h-11 items-center rounded-[3px] border px-4 text-[13px] transition-colors focus-visible:outline-1 focus-visible:outline-cyan'

type ProjectDetailsProps = {
  project: Project
  /** Phones only: the streamed answer. Desktop shows it in the chat column, so the section is left out. */
  answerText?: string | null
  onContinueInChat?: () => void
  /** The Visuals section's content (the gallery); left out when the project has none. */
  visuals?: ReactNode
}

/** The portfolio sheet's sections, in the spec's order (§5.5). */
function ProjectDetails({ project, answerText, onContinueInChat, visuals }: ProjectDetailsProps) {
  const { live, code } = project.links
  return (
    <>
      <header>
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <h2 className={SHEET_TITLE}>{project.title}</h2>
          <KindBadge kind={project.kind} />
          <span className="text-[13px] text-muted">{project.year}</span>
        </div>
        <p className={`mt-2 break-words text-cyan ${SHEET_BODY}`}>{project.oneLiner}</p>
      </header>

      {visuals && project.visuals.length > 0 && <SheetSection heading="Visuals">{visuals}</SheetSection>}

      <SheetSection heading="Stack & links">
        <ul aria-label="Stack" className="flex min-w-0 flex-wrap gap-1.5">
          {project.stack.map((tag) => (
            <li key={tag} className="max-w-full break-words rounded-[3px] border border-hairline bg-canvas px-2 py-0.5 text-[13px] text-primary">{tag}</li>
          ))}
        </ul>
        {(live || code) && (
          <div className="mt-3 flex flex-wrap gap-2">
            {live && (
              <a href={live} target="_blank" rel="noopener noreferrer" aria-label="Live site, opens in a new tab" className={`${LINK_BUTTON} border-cyan/60 text-cyan hover:bg-cyan/10`}>
                ↗ Live site
              </a>
            )}
            {code && (
              <a href={code} target="_blank" rel="noopener noreferrer" aria-label="Code, opens in a new tab" className={`${LINK_BUTTON} border-hairline text-primary hover:border-cyan hover:text-cyan`}>
                {'</>'} Code
              </a>
            )}
          </div>
        )}
      </SheetSection>

      {project.body.length > 0 && (
        <SheetSection heading="About the project">
          <MarkdownBody blocks={project.body} />
        </SheetSection>
      )}

      {answerText !== undefined && (
        <SheetSection heading="Ask about this">
          <div className="max-w-[70ch] rounded-[3px] border border-hairline bg-canvas px-4 py-3">
            <p className="break-words text-xs text-cyan">{questionForProject(project.title)}</p>
            <p aria-live="polite" className={`mt-2 whitespace-pre-wrap break-words text-primary ${SHEET_BODY}`}>{answerText || 'Working…'}</p>
          </div>
          {onContinueInChat && answerText && <ContinueInChat onClick={onContinueInChat} />}
        </SheetSection>
      )}
    </>
  )
}

export default ProjectDetails
