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
- In the mobile diagram view the header's topic nav row (About Basel / About This System) collapses (same `Collapsible`, inert while hidden) to give the diagram that height; it returns in Chat view. Hiding it never changes the corpus: a question typed in the ask box uses the current topic (shown in the footer), a node tap switches to About This System. In Diagram view the status text left of the Chat/Diagram switch reads "Select a component" until a request runs; the ask box keeps "Ask anything..." (typing still asks the current topic), and the details empty state says "Tap a component to see what runs it and ask about it." Focus on the nav moves to the Chat/Diagram toggle when it hides.
- Mobile header has 8px of top padding (`pt-2`, the 4px grid; desktop `md:py-0` unchanged, its 72px min-height already centres the row) above the 44px name/Contact/GitHub row, so the row isn't tight against the screen edge (header is 97px in Chat). In Diagram view the nav row collapses and the header gains 8px of bottom padding (`pb-2`, animated with the same 200ms ease-out, none under reduced motion) so the row is symmetric, 8 above and 8 below, 61px total. Padding, not margin, so the background and border stay one block. `index.html`'s viewport has no `viewport-fit=cover`, so the browser already keeps the header out of the notch/status area and no `env(safe-area-inset-top)` is needed; if `viewport-fit=cover` is ever added, change it to `pt-[max(0.5rem,env(safe-area-inset-top))]`. Vertical padding doesn't affect `useFullNameFits`, which reads widths only.
- Chrome that hides (focus mode: header and footer slide away while the ask box has focus) animates grid rows, not height, respects `prefers-reduced-motion`, keeps the message list pinned to the bottom, and never moves a control while it is being tapped.
- Below md, New chat lives in the footer as an icon-only 44px "+" on the right of the footer, immediately left of the bunny/tiger capacity icon (stats stay on the left; DOM order is stats, "+", capacity icon; its tooltip is right-anchored so it never clips) (same look and long-press/tooltip behaviour as the capacity icon, via `hooks/useLongPressTooltip.ts`); there is no label-vs-icon width switching. The latency stat is a focusable control with the same tooltip. The stats stay on one line.
- Diagram: React Flow only fits once on init, so `ArchitecturePanel` refits through a `ResizeObserver` (`lib/diagramFit.ts`) on any container resize or layout switch, never on trace updates.

## Typography

JetBrains Mono is wide (~0.6em per character), so everything wraps sooner than a proportional font would.

| Role | Size | Notes |
| --- | --- | --- |
| Chat messages and answers | `text-[13px] leading-[1.6]` at every width | Primary reading text. The owner chose smaller, Claude-like reading text on 2026-09-30, overriding the earlier 14-15px (and 16px body) rule. In JetBrains Mono 13px is about 7.8px per character, close to a 16px proportional font. Never below 13px. |
| Text inputs and textareas (chat ask box) | `text-[13px] leading-[1.6]`, same as the messages | The owner wants the ask box the same size as the messages, always (2026-09-30). iOS Safari zooms the page on focus for inputs under 16px, so `index.html` adds `maximum-scale=1` to the viewport on iOS/iPadOS only: iOS ignores it for pinch-zoom (users can still zoom) but stops the focus zoom. It is not added on other platforms, where it would block pinch-zoom. Any new input must keep this in mind. |
| Secondary UI (nav, buttons, suggested questions, Contact) | `text-xs` (12px) minimum | Only for short labels, not paragraphs. Suggested-question chips are 12px with `leading-normal`. |
| Metadata (stats bar, diagram captions, tooltips) | 11px minimum | `text-[10px]` is not allowed — it's unreadable on a phone. |
| Headings | `clamp()`, e.g. `text-[clamp(1rem,0.9rem+0.5vw,1.25rem)]` | Scale smoothly instead of jumping at `md`. |

- Reading text gets `max-w-[65ch]` (≈45–75 characters per line) and a 1.6 line-height (`leading-[1.6]`; chat bubbles) or `leading-relaxed`.
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

## Manual phone preview (for the owner and for agents)

Use this to look at the mobile layout yourself, without shrinking the browser window:

1. `cd frontend && npm ci` (once), then `npm run phone`.
2. Open http://localhost:5230/phone-preview.html.

It shows the app in four phone frames side by side:
- iPhone 15 at 393×852
- iPhone SE at 375×667
- a narrow Android at 360×780, which is below the 367px point where the header name shortens to "Basel A-R"
- the smallest phone at 320×568

Each frame is a real phone-width page, so media queries and the measured layouts (name shortening, footer) behave as they do on a device.

- **API:** `/api` is proxied to the live site, so answers are real, and the rate limits, the daily budget and the shared stress-test cooldown all apply. Set `GLASSBOX_API_PROXY=http://localhost:8000` to use a local API instead.
- **Limits:** press-and-hold works with a mouse. Touch-only behaviour, such as the on-screen keyboard resizing the layout or iOS safe areas, still needs a real phone.
- **Agents:** when an agent opens this for the owner, it starts `npm run phone` in a worktree and opens the page with `open http://localhost:5230/phone-preview.html`. The Chrome extension isn't needed. Stop the server when the owner is done.
