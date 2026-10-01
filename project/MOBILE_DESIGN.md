# Mobile & responsive design rules

Rules for any frontend change in `frontend/`. The desktop layout has been decent; mobile and text sizing have drifted because each session picked its own values. These rules exist so they don't.

## Before starting UI work

- Load the `impeccable` skill (installed locally from `skills-lock.json` into the gitignored `.agents/skills/impeccable`, symlinked at `.claude/skills/impeccable`; a fresh worktree or clone won't have it — load it from the main checkout's install, or reinstall from the lockfile if missing) for any design/layout/typography change. Its "verify in bounded passes" rule applies here.
- Design tokens live in `frontend/src/index.css` `@theme`. Read the comments there first — two Tailwind v4 pitfalls are documented (no var()-to-var() aliasing; never name a token after a reserved scale key like `base`/`sm`/`xl`).

## Layout

- **Mobile-first.** Write the base classes for a 375px phone, then add larger overrides. `md:` (768px) is the main layout breakpoint and must stay in sync with `useMediaQuery('(min-width: 768px)')` in `App.tsx`; `sm:` (640px) is fine for small refinements (e.g. `StatsBar`).
- **Viewport height: `h-dvh`, never `h-screen`/`100vh`,** on anything full-height. `body` has `overflow: hidden`, so on iOS Safari a `100vh` shell pushes the bottom (StatsBar, chat input) behind the browser toolbar with no way to scroll to it.
- **No fixed pixel heights/widths for boxes that hold flowing text** (headers, bars, chat bubbles, inputs). Use `min-h-*`, padding, and `rem`. Fixed px is fine for icons, hairlines, and **React Flow diagram nodes** — the 124×42 node size in `ArchitecturePanel.tsx` is deliberate (`docs/DESIGN.md`: React Flow needs fixed node dimensions and handle positions or nodes/arrows vanish during updates); size labels to fit the node instead.
- **Flex/grid children that hold text get `min-w-0`,** otherwise long words or code refuse to shrink and cause horizontal scroll.
- **Layout switches via CSS breakpoints.** JS (`useMediaQuery`) only to avoid *mounting* something heavy (the React Flow diagram) — not for styling.
- Mobile views that replace content in place (the diagram view) are a history entry: browser Back and Escape return, and focus moves back to the control that opened them. Any future mobile overlay uses `dvh`, respects `env(safe-area-inset-bottom)`, traps Escape, and restores focus on close.
- Chrome that hides (focus mode: header and footer slide away while the ask box has focus) animates grid rows, not height, respects `prefers-reduced-motion`, keeps the message list pinned to the bottom, and never moves a control while it is being tapped.
- Below md, New chat lives in the footer as an icon-only 44px "+" on the right of the footer, immediately left of the bunny/tiger capacity icon (stats stay on the left; DOM order is stats, "+", capacity icon; its tooltip is right-anchored so it never clips) (same look and long-press/tooltip behaviour as the capacity icon, via `hooks/useLongPressTooltip.ts`); there is no label-vs-icon width switching. The latency stat is a focusable control with the same tooltip. The stats stay on one line.
- Diagram: React Flow only fits once on init, so `ArchitecturePanel` refits through a `ResizeObserver` (`lib/diagramFit.ts`) on any container resize or layout switch, never on trace updates.

## Typography

JetBrains Mono is wide (~0.6em per character), so everything wraps sooner than a proportional font would.

| Role | Size | Notes |
| --- | --- | --- |
| Chat messages and answers | `text-sm` (14px) mobile → `md:text-[15px]`/`text-base` | Primary reading text. Never below 14px. |
| Text inputs and textareas (chat ask box) | `text-base` (16px) at every width | iOS Safari zooms the page on focus for any input under 16px. |
| Secondary UI (nav, buttons, suggested questions, Contact) | `text-xs` (12px) minimum | Only for short labels, not paragraphs. |
| Metadata (stats bar, diagram captions, tooltips) | 11px minimum | `text-[10px]` is not allowed — it's unreadable on a phone. |
| Headings | `clamp()`, e.g. `text-[clamp(1rem,0.9rem+0.5vw,1.25rem)]` | Scale smoothly instead of jumping at `md`. |

- Reading text gets `max-w-[65ch]` (≈45–75 characters per line) and `leading-relaxed`.
- Streamed/model text and anything user-provided gets `break-words` (`overflow-wrap: anywhere` for URLs/code) so it can't overflow its bubble.
- `whitespace-nowrap` only on things guaranteed short (the name, single-word buttons) — and verify at 375px.
- Tap targets ≥ 44×44px on mobile (pad small buttons, don't enlarge the text).

## Verification (required before calling UI work done)

1. Run the app (`frontend`: `npm run dev`; API per `README.md`).
2. Screenshot with the `claude-in-chrome` tools at **375, 414, 768, 1024, 1440px** widths.
3. Check at each width: no horizontal scroll, no text clipped or overflowing, nothing overlapping, bottom bar and chat input visible, tap targets usable, diagram view (mobile: all 11 nodes visible, details panel collapses/reopens, Back returns to chat) and diagram nodes + arrows visible (desktop).
4. Fix everything found in **one batch**, re-check once, stop. Don't polish in an open-ended loop.
5. `npm run lint && npm run build` pass.
6. The Codex review gate (`project/orchestration/codex-reviewer.md`) for a UI change should include the same width checklist.
