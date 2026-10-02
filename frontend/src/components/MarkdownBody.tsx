// "About the project": paragraphs, lists and links only (lib/portfolio.ts
// parseBody). Text is rendered as React text, so any HTML in the source is
// shown as text; there is no dangerouslySetInnerHTML.
import { Fragment } from 'react'
import type { Block, Inline } from '../lib/portfolio'
import { SHEET_BODY } from './DetailsSheet'

function Inlines({ inlines }: { inlines: readonly Inline[] }) {
  return (
    <>
      {inlines.map((inline, n) => inline.kind === 'link' ? (
        <a key={n} href={inline.href} target="_blank" rel="noopener noreferrer" className="break-words text-cyan underline underline-offset-4 transition-colors hover:text-primary">
          {inline.text}
        </a>
      ) : (
        <Fragment key={n}>{inline.text}</Fragment>
      ))}
    </>
  )
}

function MarkdownBody({ blocks }: { blocks: readonly Block[] }) {
  return (
    <div className={`space-y-3 break-words text-primary/90 ${SHEET_BODY}`}>
      {blocks.map((block, n) => {
        if (block.kind === 'p') return <p key={n}><Inlines inlines={block.inlines} /></p>
        const items = block.items.map((item, i) => <li key={i}><Inlines inlines={item} /></li>)
        return block.kind === 'ul'
          ? <ul key={n} className="list-disc space-y-1 pl-5">{items}</ul>
          : <ol key={n} className="list-decimal space-y-1 pl-5">{items}</ol>
      })}
    </div>
  )
}

export default MarkdownBody
