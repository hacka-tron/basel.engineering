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
- Below md the question topic is a radio group of chips directly above the ask box, in **Chat view only** (`components/TopicChips.tsx`: "Asking about (● Basel) (○ System)", one 44px row, `role="radiogroup"`/`role="radio"` with `aria-checked`, roving tabindex, arrows/Home/End move and select, accessible names are the full topic names). It drives the same topic state and handler as the desktop nav. The chips row sits inside the ask box's container in place of its 8px top padding, so it adds 36px there; the header gave back 36px (one row instead of two), so Chat's message area equals the old two-row header layout. Diagram view unmounts the chips (owner, 2026-10-01), so nothing hidden stays focusable and the diagram has the same height as before the chips; if a chip had focus when the view switches (e.g. browser Forward), focus moves to the Diagram toggle. Hiding them never changes the topic: a question typed in Diagram view uses the current topic, and a node tap switches to About This System. In Diagram view the status text left of the Chat/Diagram switch reads "Select a component" until a request runs; the ask box keeps "Ask anything...", and the details empty state says "Tap a component to see what runs it and ask about it." The owner compared three options live (a topic dropdown in the header row, these chips, and the old two-row header) and chose the chips plus the envelope Contact; see `project/status/2026-09-30-mobile-topic-dropdown.md`.
- Mobile header is one 44px row (name ... envelope, GitHub) with symmetric 8px padding (`py-2`, the 4px grid; desktop `md:py-0` unchanged, its 72px min-height already centres the row), 61px total in both Chat and Diagram views. Padding, not margin, so the background and border stay one block. The row is `flex-nowrap` and the Contact/GitHub group is `max-md:shrink-0`, so if the name does not fit `useFullNameFits` swaps in "Basel A-R" (measured, 16px minimum gap, 8px hysteresis) instead of squeezing the icons. With the envelope the full name fits on every phone from 292px: short at 291px and below, full again from 300px. `Collapsible`'s inner wrapper is `min-w-0`, otherwise the nowrap row's min-content width (292px) would make the header wider than the screen and the name would never shorten. `index.html`'s viewport has no `viewport-fit=cover`, so the browser already keeps the header out of the notch/status area and no `env(safe-area-inset-top)` is needed; if `viewport-fit=cover` is ever added, change the top padding to `pt-[max(0.5rem,env(safe-area-inset-top))]`. Vertical padding doesn't affect `useFullNameFits`, which reads widths only.
- Contact is an envelope icon button at every width (`components/ContactReveal.tsx`, owner 2026-10-01), directly left of the GitHub icon and the same size (24px, 28px from `sm`, back to 24px from `md` because the owner found 28px slightly too big on a laptop (2026-10-01); a 44px target below md). Its accessible name is "Copy email". Hover (hover-capable devices only) or keyboard focus shows a "Copy email" tooltip under it; a click copies the address and the same bubble says "Email copied" (2s), or shows the address itself for 5s if the Clipboard API and the execCommand fallback both fail. The bubble is absolutely positioned and right-aligned to the button, so the row never changes width (at 320px the address bubble spans x 58-270); results are announced through a separate polite live region.
- Chrome that hides (focus mode: header and footer slide away while the ask box has focus) animates grid rows, not height, respects `prefers-reduced-motion`, keeps the message list pinned to the bottom, and never moves a control while it is being tapped.
- Below md, New chat lives in the footer as an icon-only 44px "+" on the right of the footer, immediately left of the bunny/tiger capacity icon (stats stay on the left; DOM order is stats, "+", capacity icon; its tooltip is right-anchored so it never clips) (same look and long-press/tooltip behaviour as the capacity icon, via `hooks/useLongPressTooltip.ts`); there is no label-vs-icon width switching. The latency stat is a focusable control with the same tooltip. The stats stay on one line.
- Diagram: React Flow only fits once on init, so `ArchitecturePanel` refits through a `ResizeObserver` (`lib/diagramFit.ts`) on any container resize or layout switch, never on trace updates.

## Typography

JetBrains Mono is wide (~0.6em per character), so everything wraps sooner than a proportional font would.

| Role | Size | Notes |
| --- | --- | --- |
| Chat messages and answers | `text-[13px] leading-[1.6]` at every width | Primary reading text. The owner chose smaller, Claude-like reading text on 2026-09-30, overriding the earlier 14-15px (and 16px body) rule. In JetBrains Mono 13px is about 7.8px per character, close to a 16px proportional font. Never below 13px. |
| Text inputs and textareas (chat ask box) | `text-[13px] leading-[1.6]`, same as the messages | The owner wants the ask box the same size as the messages, always (2026-09-30). iOS Safari zooms the page on focus for inputs under 16px, so `index.html` adds `maximum-scale=1` to the viewport on iOS/iPadOS only: iOS ignores it for pinch-zoom (users can still zoom) but stops the focus zoom. It is not added on other platforms, where it would block pinch-zoom. Any new input must keep this in mind. |
| Secondary UI (nav, buttons, suggested questions, tooltips like Contact's) | `text-xs` (12px) minimum | Only for short labels, not paragraphs. Suggested-question chips are 12px with `leading-normal`. |
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

It shows the app in five phone frames side by side:
- iPhone 15 at 393×852
- iPhone SE at 375×667
- a narrow Android at 360×780
- the smallest phone at 320×568
- a Galaxy Fold cover screen at 280×653, below the 292px point where the header name shortens to "Basel A-R"

Each frame is a real phone-width page, so media queries and the measured layouts (name shortening, footer) behave as they do on a device.

- **API:** `/api` is proxied to the live site, so answers are real, and the rate limits, the daily budget and the shared stress-test cooldown all apply. Set `GLASSBOX_API_PROXY=http://localhost:8000` to use a local API instead.
- **Limits:** press-and-hold works with a mouse. Touch-only behaviour, such as the on-screen keyboard resizing the layout or iOS safe areas, still needs a real phone.
- **Agents:** when an agent opens this for the owner, it starts `npm run phone` in a worktree and opens the page with `open http://localhost:5230/phone-preview.html`. The Chrome extension isn't needed. Stop the server when the owner is done.

## Owner decisions (index)

One line per standing mobile/UI decision, so a session can check them at a glance. The sections above (and the code comments they point to) have the detail; record new decisions both there and here.

- **Text size:** 13px chat messages and a 13px ask box at every width; `maximum-scale=1` is added on iOS/iPadOS only, to stop focus zoom without blocking pinch-zoom elsewhere (2026-09-30, #77).
- **Header name:** the full name shows whenever its measured natural width fits beside the other row items with at least 16px to spare; otherwise "Basel A-R". Returning to the full name needs 8px more room, so a width on the threshold can't flicker (`lib/headerName.ts`, `useFullNameFits`; #72).
- **Diagram status text:** "Select a component" until a request runs (#76, #79).
- **Stress test on mobile:** every tap switches to the Diagram view, at tap time (#67, #70).
- **New chat on mobile:** an icon-only "+" on the right of the footer, beside the capacity icon (#69).
- **Header layout, topic chips, Contact (#78, 2026-10-01):** a one-row mobile header with `py-2`; the topic chips ("Asking about") sit above the ask box in Chat view only and unmount in Diagram view; Contact is an envelope icon ("Copy email") at every width. Detail in the Layout section above.
- **Error replies never blame the visitor:** failures read as the backend's fault ("something's wrong with the backend", "my thoughts got tangled"), never "your question" or "you" (`lib/errorReplies.ts`).
- **"I don't know" swaps are exact-match only:** the playful replies replace the answer only when it is exactly the canonical abstention ("I don't know from what I have.", flagged `done.abstained`); a partial answer that merely contains the phrase is shown as written. History always keeps the canonical sentence (`lib/idkReplies.ts`).
- **Desktop topic control stays in the header (2026-10-01):** "About Basel | About This System" tabs in the desktop header. Pills above the desktop ask box were tried (#82) and reverted the same day at the owner's request; pills are for phones only (Chat view, #78).
- **Footer latency lights up (2026-10-01):** the latency readout brightens from muted on hover, keyboard focus and press, like the capacity icon (from #82, kept after the revert).
- **No "cached" in the footer (2026-10-01):** the latency shows only the number. Suggested questions are pre-warmed, so a visible "· cached" appeared on every one; the cache hit is mentioned only in the hover/long-press tooltip.
