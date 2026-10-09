import { useId } from 'react'
import type { Project } from '../lib/portfolio'
import { initials, placeholderColors, stackPreview } from '../lib/portfolioView'

/** The project's cover, else its first visual, else a generated placeholder (decorative: the card's text names it). */
function ProjectThumb({ project, className }: { project: Project; className: string }) {
  // A cover's width/height are only intrinsic-size hints: both thumb boxes are fixed-aspect with object-cover.
  const first = project.cover ? { src: project.cover, width: 1600, height: 1000 } : project.visuals[0]
  if (first) {
    return <img src={first.src} alt="" width={first.width} height={first.height} loading="lazy" decoding="async" className={`block rounded-[2px] border border-hairline bg-canvas object-cover ${className}`} />
  }
  const { from, to } = placeholderColors(project.slug)
  return (
    <span
      aria-hidden="true"
      className={`flex items-center justify-center rounded-[2px] border border-hairline text-[clamp(1rem,0.8rem+1vw,1.5rem)] font-semibold tracking-tight text-white/85 ${className}`}
      style={{ backgroundImage: `linear-gradient(135deg, ${from}, ${to} 75%)` }}
    >
      {initials(project.title)}
    </span>
  )
}

type ProjectCardProps = {
  project: Project
  selected: boolean
  onPress: () => void
  /** Set on the first card so "See projects →" can focus it. */
  buttonRef?: (element: HTMLButtonElement | null) => void
}

/**
 * A grid card (a toggle button). From 390px it is a column: thumbnail, title
 * and year, the one-liner (3 lines at most), stack tags. Below 390px (one
 * column) it is a row with a 64px square thumbnail on the left.
 */
function ProjectCard({ project, selected, onPress, buttonRef }: ProjectCardProps) {
  const descriptionId = useId()
  const { shown, rest } = stackPreview(project.stack)
  return (
    <button
      ref={buttonRef}
      type="button"
      aria-pressed={selected}
      aria-label={`${project.title}, ${project.year}`}
      aria-describedby={descriptionId}
      onClick={onPress}
      className={`flex h-full w-full min-w-0 flex-row gap-3 rounded-[3px] border p-2 text-left transition-colors focus-visible:outline-1 focus-visible:outline-cyan min-[390px]:flex-col min-[390px]:gap-2 md:p-3 ${selected ? 'border-cyan bg-canvas' : 'border-hairline hover:border-muted'}`}
    >
      <ProjectThumb project={project} className="aspect-square w-16 shrink-0 self-start min-[390px]:aspect-[16/9] min-[390px]:w-full" />
      <span className="flex min-w-0 flex-1 flex-col gap-2">
        <span className="flex min-w-0 items-baseline justify-between gap-2">
          <span className={`min-w-0 break-words text-xs font-medium ${selected ? 'text-cyan' : 'text-primary'}`}>{project.title}</span>
          <span className="shrink-0 text-[11px] text-muted">{project.year}</span>
        </span>
        <span id={descriptionId} className="line-clamp-3 break-words text-xs leading-relaxed text-muted">{project.oneLiner}</span>
        <span className="mt-auto flex min-w-0 flex-wrap gap-1">
          {shown.map((tag, index) => (
            <span key={`${index}:${tag}`} className="max-w-full truncate rounded-[2px] bg-canvas px-1.5 text-[11px] leading-[1.6] text-muted">{tag}</span>
          ))}
          {rest > 0 && <span className="px-1 text-[11px] leading-[1.6] text-muted">+{rest}</span>}
        </span>
      </span>
    </button>
  )
}

export default ProjectCard
