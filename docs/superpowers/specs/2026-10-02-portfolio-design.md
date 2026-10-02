# Portfolio topic, shared details sheet, and "Open to work" footer

**Status:** design approved by the owner on 2026-10-02 after four rounds of live mocks. Next: implementation plan.
**Mocks (throwaway, not to be merged):** branch `mock/portfolio` (portfolio, sheets) and branch `mock/hire-banner` (footer callout). Use them as a visual reference only; production code is written fresh from this spec.

## 1. Goal

basel.engineering doubles as Basel's general portfolio, for recruiters and for freelance clients. The visitor should be able to:

- see Basel's other projects, with visuals, without leaving the one-screen app;
- ask the chatbot about those projects, like the existing two topics;
- see that Basel is open to full-time work first and freelancing second, and copy his email.

None of this may take height from the chat during a conversation, or change how the existing diagram works except where §5 says so.

## 2. Decisions (owner, 2026-10-02)

| Question | Decision |
|---|---|
| Where portfolio content lives | Public, in this repo: `corpus/portfolio/<slug>.md`, one file per project. |
| How the portfolio is reached | A third chat topic, **Portfolio**, beside About Basel and About This System. |
| Desktop right pane | Follows the topic. Portfolio topic shows the portfolio; the other two show the diagram. No tabs. |
| Phone views | A three-way toggle **Chat \| Diagram \| Portfolio**, always shown, whatever the topic. |
| Portfolio layout | Card grid (mock variant A). The compact list (variant B) was rejected. |
| Project details | A pull-up sheet over the grid covering about 80% of the region, so the content behind it shows. Sections: title, one-liner, Visuals, Stack & links, About the project, and (phones only) Ask about this. |
| Phone diagram details | The same pull-up sheet (about 80%), replacing today's panel capped at 40%. Desktop diagram details are unchanged. |
| Text size in sheets | 15px body on phones, up to 16px on desktop, line-height 1.6, max 70ch. Deliberately larger than the 13px reading size in `project/MOBILE_DESIGN.md`; record it there. |
| Hire callout | Footer item "● Open to work" opening a popover: "Open to full-time work and freelancing". Ships separately, first. |
| "See portfolio" link in the popover | Left out until the portfolio has shipped; added in the portfolio frontend PR (§5.7). |

## 3. Delivery: three PRs, in order

1. **Footer "Open to work" callout** (§4). Frontend only, small, independent.
2. **Portfolio corpus, backend** (§6). Ingest, retrieval, API and DB accept `portfolio`; a placeholder project file. Visitors see no change yet.
3. **Portfolio frontend and the shared details sheet** (§5, §7). Depends on 2 being deployed (the topic must answer). Adds the popover's "See portfolio →" link.

Each PR gets the normal review gate, a status report in `project/status/`, and the `MOBILE_DESIGN.md` and `SNAPSHOT.md` updates it implies.

## 4. Footer "Open to work" callout (PR 1)

**Visitor-visible:**

- Footer, right group, left of New chat (phones) or Stress test (desktop): a green dot plus "Open to work". It is a button with a 44px minimum target.
- Below 360px the label hides and only the dot shows (accessible name stays "Open to work"). Below 300px (Fold cover screen) the item is left out entirely; the header envelope still copies the email there.
- Tap or click opens a popover above the footer item:
  - headline "Open to full-time work and freelancing";
  - body "Talking to teams about full-time roles and taking on freelance projects. Copy my email and say hi.";
  - one button, **Copy email**, with the same result feedback as the header envelope ("Email copied", or the address itself if the clipboard is unavailable).
- The popover closes on Escape (focus back to the trigger), an outside tap, or a second tap on the trigger. It stays on screen at every width (checked down to 280px).
- In focus mode the footer slides away as now, with the item.

**Code:**

- New `frontend/src/lib/contact.ts`: the email address and the copy logic (Clipboard API, then the `execCommand` fallback), moved out of `ContactReveal.tsx`, which then imports them. The address stays out of `index.html`.
- New `frontend/src/components/OpenToWork.tsx`: the footer button and popover. `StatsBar` renders it.
- Wording constants live with the component, not in a mock module.

**Width fix in the same PR:** with real numbers ("1840ms | 128 queries") the item fits at 360px only after removing the label's side padding (20px to spare after that). A long latency reading ("total 12345ms", shown when a request returns no answer text) already overflows today's footer at 280px and would overflow at 320–375px with the new item. Shorten that reading on phones (for example "12.3s") so the footer never scrolls sideways at 280–393px with any value.

**Tests:** unit tests for `contact.ts` (copy success, fallback, failure shows the address); a component test for open/close and Escape focus return. Phone preview screenshots at 280, 320, 360, 375, 393 and desktop at 1024 and 1280, popover open and closed.

## 5. Frontend layout (PR 3)

### 5.1 Topic

- `Corpus` becomes `'basel' | 'system' | 'portfolio'`, sent to the API as `about_me`, `about_system`, `portfolio`.
- Desktop header nav gains **Portfolio**. Phone chips gain a third chip, **Portfolio**.
- Three chips plus "Asking about" do not fit on phones (about 400px). Below 440px the "Asking about" label is screen-reader-only (the radiogroup keeps it as its accessible name); below 360px chip padding tightens; below 320px the chip dots hide. All three chips fit at 280px.
- Each topic keeps its own conversation, as today. The Portfolio conversation is saved like the others.
- Suggested questions for Portfolio, in `frontend/src/suggested-questions.json` under `portfolio` (also read by the answer-cache warm-up): "What can Basel build for me?", "Which project is most like a SaaS app?", "Is Basel available for freelance work?". The owner may change these when the content lands.

### 5.2 Desktop

- The right pane follows the topic: Portfolio shows the portfolio panel; About Basel and About This System show the diagram exactly as today. Switching topic swaps the pane; there are no tabs.
- Only one `ArchitecturePanel` is ever mounted; it unmounts while the portfolio shows.
- Picking a project sets the topic to Portfolio (it already is, on desktop).
- **Stress test while on Portfolio:** the run and the shake happen; the pane stays on the portfolio. The workers are visible after switching topic. (Accepted trade-off; the owner rejected tabs.)
- Questions on the Portfolio topic still run through the whole pipeline; the diagram animation is simply not on screen.

### 5.3 Phones

- `PipelineStrip`'s segmented control becomes **Chat | Diagram | Portfolio**, always shown.
  - From 360px: text labels (the control is about 222px wide).
  - Below 360px: icon-only segments (speech bubble, node graph, 2×2 grid), each 44px wide with the full name as accessible name and tooltip.
  - The status text left of it reads "Select a component" in Diagram view and "Select a project" in Portfolio view, truncating with an ellipsis when short of room.
- **History:** opening Diagram or Portfolio from Chat pushes one entry; switching between Diagram and Portfolio replaces it. Back always returns to Chat; Forward reopens the last non-Chat view. Returning to Chat focuses the segment of the view that was left. This extends `lib/diagramNav.ts`, which should be generalised from "diagram" to "the non-Chat view".
- Topic chips show in Chat view only, as today. Choosing a chip never changes the view.
- Picking a project switches the topic to Portfolio; tapping a diagram component switches it to About This System (as today).
- A stress tap still opens Diagram view (as today), whatever the topic.

### 5.4 Portfolio panel

- **Header bar:** "Portfolio · N projects" on desktop; screen-reader-only on phones (the floating header is above it).
- **Card grid:** each card has a thumbnail (the project's first visual, or a generated placeholder when it has none), title, year, one-liner clamped to 3 lines, and stack tags (first three, then "+N").
  - Desktop pane: 2 columns, 3 from 1280px.
  - Phones: 2 columns from 390px; below that, single-column rows with a small square thumbnail on the left.
- The grid scrolls inside the panel; the page never scrolls. When a project is selected, its card is scrolled into the strip left visible above the sheet and shows a selected outline.
- Cards are buttons. Selecting one opens the sheet (§5.5) and sends "Tell me about <title>" on the Portfolio topic, like component inspect does for System.
- With nothing selected, the bottom of the panel is the 44px locked bar "Select a project for details" (focusable, `aria-disabled`), mirroring the diagram's bar.

### 5.5 Shared details sheet

One component, `frontend/src/components/DetailsSheet.tsx`, used by the portfolio panel (desktop and phones) and by the phone diagram. The desktop diagram's details panel is not changed.

- **Height:** about 80% of its region at every width, so a strip of the grid or diagram shows above it. The strip behind is subtly dimmed. Desktop portfolio already uses 80%; phones move from about 99% (mocks rounds 2–4) to 80%.
- **Opening:** a 260ms slide-up; none with `prefers-reduced-motion`.
- **Closing:** the 44px chevron button, Escape, browser Back, or a tap on empty space in the uncovered strip. Closing deselects and moves focus to the locked bar. Escape order is unchanged: the lightbox first, then deselect, then (phones) back to Chat. See `lib/escapeKey.ts`.
- **Ask box:** on phones the sheet stops above the pipeline strip, so the ask box stays usable while it is open.
- **Typography:** title `clamp(18px, …, 24px)`; body 15px on phones, rising to 16px at 1280; line-height 1.6; max line length 70ch; uppercase section labels; wider padding than today's panel.

**Portfolio sheet sections, in order:**

1. Title row: name, kind badge (`personal` or `freelance`), year.
2. One-liner, in the accent colour.
3. **Visuals** (§5.6), when the project has any.
4. **Stack & links:** stack tags; "↗ Live site" and "</> Code" buttons (44px), each shown only when the link exists. Links open in a new tab with `rel="noopener noreferrer"`.
5. **About the project:** the Markdown body, rendered as text paragraphs, lists and links only. No raw HTML.
6. **Ask about this** (phones only): the question, the streamed answer, and "Continue in chat →". On desktop the answer appears in the chat column beside the sheet, so the section is left out.

**Phone diagram sheet sections:** component name and what runs it; What it does; About This System answer with "Continue in chat →"; Retrieved chunks. These are today's details, laid out in the sheet.

**The strip above the sheet** (mock round 5):

- Portfolio: the selected card scrolls to the top of the grid, so it sits in the strip.
- Phone diagram: the view pans, at the same zoom, so the selected node sits in the middle of the strip; deselecting returns to the normal fitted view.
- A tap on a different card or node in the strip switches the sheet straight to that item (and asks about it). A tap on empty grid or diagram space closes the sheet and deselects.
- Measured strip heights: about 68px at 320×568, 88px at 375×667, 125px at 393×852, 134px on a 1280×800 desktop pane.

### 5.6 Visuals gallery and lightbox

- A horizontal scroll-snap row at a fixed height (`clamp(9rem, 42vw, 15rem)`); each image's width follows its declared aspect, so phone screenshots (9:19.5) sit beside desktop ones (16:10, 4:3). Optional caption under each.
- Dots plus a "1 / N" count. Prev/next buttons on desktop only (phones swipe).
- Tapping an image opens a full-screen lightbox: the image at its largest fit, caption, ✕, prev/next and the arrow keys. It closes on ✕, a backdrop tap, or Escape (which closes only the lightbox). It is a modal dialog with a real focus trap (the mock's was not); focus returns to the thumbnail.
- Images are `loading="lazy"` with explicit width and height from the aspect, so nothing shifts as they load.

### 5.7 Footer link

The footer popover (§4) gains "See portfolio →" in this PR. It selects the Portfolio topic and, on phones, opens the Portfolio view.

## 6. Portfolio corpus and backend (PR 2)

### 6.1 Content format

`corpus/portfolio/<slug>.md`, public. Frontmatter carries the card and sheet fields; the body is "About the project" and the text the chatbot indexes.

```markdown
---
title: JobPilot
one_liner: Job-search copilot that tailors applications and tracks every lead.
kind: personal          # personal | freelance
year: 2026
order: 1                # grid order, ascending
stack: [TypeScript, React, FastAPI, Postgres]
links:
  live: https://example.com     # optional
  code: https://github.com/...  # optional
visuals:                # optional; files live in frontend/public/portfolio/<slug>/
  - src: jobpilot/board.png
    alt: Pipeline board with applications grouped by stage
    caption: Pipeline board, from saved to offer.
    aspect: 16/10       # 16/10 | 4/3 | 9/19.5
draft: false            # true: not built into the site and not indexed
---

The problem, what was built, how it works, and what was interesting.
```

- Images are committed under `frontend/public/portfolio/<slug>/` and served from the site's own origin; the CSP (`img-src 'self'`) forbids anything else.
- PR 2 ships one placeholder file, `corpus/portfolio/_example.md` with `draft: true`, whose comments explain every field. The owner fills in real projects later.
- A schema check (run in CI) rejects a file with a missing required field (`title`, `one_liner`, `kind`, `year`, `stack`), an unknown `kind` or `aspect`, a visual whose `src` does not exist, a missing `alt`, or a non-`https` link.

### 6.2 Backend changes

All places that hard-code the two corpora gain `portfolio`:

- `services/glassbox/ingest/scanner.py`: scan `corpus/portfolio/**/*.md` as corpus `portfolio`, skipping `draft: true` files and hidden paths, never following symlinks (same rules as the private About Basel scan). Index the body and, as a short preface, the title, one-liner, kind, year, stack and links, so questions like "which projects use React?" retrieve.
- `services/glassbox/ingest/sweep.py`, `services/glassbox/warm.py`: `CORPORA` gains `portfolio`.
- `services/glassbox/retrieval/search.py` and `services/glassbox/api/ask.py`: accept `portfolio`.
- `services/glassbox/db/models.py` plus a new Alembic migration (`0007`): widen both `corpus` ENUM columns to include `portfolio`. Adding an ENUM value at the end is an in-place change in MySQL 8, but check it against the table sizes and note it in the migration.
- The personal-data guard (`privacy.py`) also runs over portfolio documents at ingest. They are public, but a client's details pasted into a write-up should still be caught.
- `docs/DESIGN.md` (corpora, §7.3 suggested questions) and `docs/DESIGN-003-ingestion.md` (sources) are updated in the same PR.

### 6.3 Not in scope

- No RAG evaluation runs, free or paid, until the owner has added real portfolio content (owner rule). The golden set gets no portfolio questions yet.
- No change to prompts or models. If answers about projects need a different system prompt, that is a follow-up after real content exists.

## 7. Frontend build of the portfolio data (PR 3)

- A Vite import of `corpus/portfolio/*.md` as raw text (`import.meta.glob` with `server.fs.allow` for the repo root, or a small build-time plugin) reads the Markdown files, parses frontmatter, drops `draft: true`, sorts by `order`, and exposes a typed `Project[]`. No runtime fetch.
- The frontmatter parser and validator are shared in spirit with §6.1's CI check; the frontend build fails on an invalid file, like CI.
- With zero non-draft projects (the state after PR 2), the Portfolio topic still works and the panel shows a short empty state: "Projects are on their way. Ask the chat in the meantime." The owner may prefer to keep the topic hidden until there is content; decide at PR 3 review.

## 8. Accessibility and behaviour checklist

- Every new control is at least 44×44px on phones and reachable by keyboard in a sensible order.
- The three-way toggle and the chips are radiogroups with roving tabindex, as the chips are today.
- Sheets: `role="region"` with the item's name as label; the locked bar is focusable and `aria-disabled`; focus moves to it when the sheet closes.
- Lightbox: `role="dialog"`, `aria-modal`, focus trap, focus restored on close.
- Reduced motion: no slide or shake animations; reduced transparency: no dimming scrim (hard edge).
- No horizontal scroll at 280, 320, 360, 375, 393, 768, 1024, 1280; the header stays one row.
- Phones held sideways still show the rotate screen; the new views stay mounted underneath like the diagram does.

## 9. Testing

- Unit: topic and corpus mapping; view-navigation history for three views (extend `diagramNav.test.ts`); Escape ordering with sheet and lightbox; portfolio frontmatter parsing and validation; `contact.ts`.
- Backend: scanner picks up `corpus/portfolio`, skips drafts and symlinks; `ask` accepts `portfolio`; migration up and down; warm-up covers the new suggested questions.
- Manual and screenshot: the phone preview (`npm run phone`) at the widths in §8, each view and sheet state; desktop at 768, 1024 and 1280. Record the measurements in each status report.

## 10. Risks and open items

- **Visitor focus on desktop:** while on the Portfolio topic the diagram, the site's main demonstration, is hidden. Accepted by the owner; revisit if analytics or feedback suggest people miss it.
- **Footer width:** the "Open to work" item leaves little room at 360px; any new footer content must be checked against it.
- **Empty portfolio:** until the owner adds projects, the topic answers from nothing useful. Decide at PR 3 whether to hide the topic until there is content (§7).
- **Bundle size:** many screenshots are fine (lazy, not in the JS bundle), but long Markdown bodies are in the bundle. Keep write-ups to a few paragraphs, or move bodies to a fetched JSON if they grow.
