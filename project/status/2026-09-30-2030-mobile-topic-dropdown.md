# Mobile header: one row, topic chips in Chat view, envelope Contact (2026-09-30 20:30 PT)

**Status:** In review, PR [#78](https://github.com/hacka-tron/basel.engineering/pull/78) · **Scope:** `frontend/`. Phones: header, topic chips, Contact. Desktop: only the Contact control changes (text to envelope icon).

## TL;DR

The phone header is now a single 61px row (name, envelope, GitHub) in both Chat and Diagram views. The question topic moved out of the header into two chips directly above the ask box, "Asking about (● Basel) (○ System)", shown in Chat view only. Contact is an envelope icon ("Copy email") at every width, desktop included. The owner compared three options live (a topic dropdown in the header row, the chips, and the current two-row header) and chose the chips plus the envelope icon. Chat and Diagram heights equal origin/main at every phone width; desktop differs from main only in the Contact area.

## What changed for a visitor

- **Phone header:** one row, 8px padding above and below (61px), the same in Chat and Diagram. The full name now fits from 292px, so it shows on every phone (it shortens to "Basel A-R" at 291px and below and returns from 300px; before this PR it shortened below 367px).
- **Topic (phones):** chips directly above the ask box in Chat view. Tapping one switches the topic exactly as the desktop nav does (same handler: one conversation per topic, component selection cleared when going to About Basel). Diagram view hides the chips and keeps the topic: a question typed there uses the current topic, and tapping a component switches to About This System, as before.
- **Contact (all widths):** an envelope icon the size of the GitHub mark beside it. Hover or keyboard focus shows "Copy email"; a click copies the address and shows "Email copied". If copying fails, the bubble shows the address itself for 5 seconds so it can be read. The bubble floats under the icon, so the row never moves.
- **Removed:** the Diagram-view collapse of the old nav row, its focus rescue, and the animated bottom padding.

## How it works

```
header  [Basel Abdel-Rahman ............ (✉) (gh)]   61px, py-2, flex-nowrap
main    messages (Chat) / diagram (Diagram)
        PipelineStrip  [stages ...      Chat|Diagram]
        input box  [Asking about (● Basel) (○ System)]  Chat view only: 44px, replaces the 8px top padding
                   [Ask anything...              ][→]
footer  stats ... + (bunny)
```

- `components/TopicChips.tsx`: `role="radiogroup"` labelled "Asking about", buttons with `role="radio"` and `aria-checked`, roving tabindex, arrows/Home/End move and select (wrapping). Accessible names are the full topic names. App passes it to `Chat`'s `inputTopic` slot only below md and only in Chat view; in Diagram view it unmounts (no hidden focusable control). If a chip has focus when it unmounts (e.g. browser Forward into Diagram view), a layout-effect cleanup hands focus to the Diagram toggle.
- `components/ContactReveal.tsx`: one envelope button everywhere, `aria-label="Copy email"`. A single out-of-flow bubble is the tooltip while idle (shown on `@media (hover: hover)` hover or `focus-visible`) and the result after a click; a separate `sr-only` polite live region announces the result.
- `components/Collapsible.tsx`: the inner wrapper is `min-w-0`. Without it the one-row header's min-content width (292px) made the grid item grow past the screen, so on very narrow screens the name never shortened and the row overflowed.

## Measurements (headless Chrome, production builds, origin/main vs this branch)

| Width × height | Header Chat (main → branch) | Header Diagram | Name | Chat message area | Diagram |
|---|---|---|---|---|---|
| 320×740 | 97 → 61 | 61 → 61 | Basel Abdel-Rahman (main: Basel A-R) | 475 → 475 | 467 → 467 |
| 360×740 | 97 → 61 | 61 → 61 | Basel Abdel-Rahman (main: Basel A-R) | 475 → 475 | 467 → 467 |
| 375×740 | 97 → 61 | 61 → 61 | Basel Abdel-Rahman | 475 → 475 | 467 → 467 |
| 390×740 | 97 → 61 | 61 → 61 | Basel Abdel-Rahman | 475 → 475 | 467 → 467 |
| 414×740 | 97 → 61 | 61 → 61 | Basel Abdel-Rahman | 475 → 475 | 467 → 467 |
| 375×667 | | | | 402 → 402 | 394 → 394 |

One row at every width (all header items share one top), no horizontal scroll, every header target 44×44. Gap between the name and the icons: 44px at 320, 97px at 375. The chips row is 44px tall; it costs 36px in Chat, exactly what the one-row header gave back.

## Verified

- `npm run lint`, `tsc -b`, `npm test` (58 pass), `npm run build`.
- Topic and corpus (mocked `/api/ask`, request bodies checked at 375px): Basel chip sent `about_me`; System chip sent `about_system`; in Diagram view (no chips mounted) a typed question sent `about_system` while System was current and `about_me` while Basel was current; a component tap then a typed question sent `about_system` and the chips show System back in Chat.
- Keyboard: Tab order on a phone is Copy email, GitHub, suggestions, pipeline, Chat, Diagram, checked chip, ask box, Send. ArrowRight selects System and wraps back to Basel. Diagram toggle, history Back to Chat, focus a chip, history Forward: focus lands on the Diagram toggle.
- Contact: Enter on the focused envelope with the clipboard blocked shows the address (320px: bubble x 58–270, on screen; header stays 61px); with the clipboard allowed it shows "Email copied". Desktop (1024): hover shows "Copy email", moving away hides it, Shift+Tab focus shows it, Enter shows "Email copied" and the live region announces it.
- Desktop pixel diff vs origin/main at 768×900, 1024×900, 1440×900: 692 differing pixels each, all inside the Contact area (box x 613–695 / 869–951 / 1285–1367, y 25–46; that is where "Contact me" was and the envelope is). The name, nav, GitHub icon (same position) and everything below the header are identical.
- Screenshots: `topic-dropdown-final/` in the session scratchpad (`m<width>-chat|diagram.png`, envelope states, `d<width>-main|branch.png`, desktop tooltip and "Email copied").

## Options compared (history)

1. Topic dropdown in the header row (commit a0b0c98): worked (44px items, on screen at 320px) but with "Contact me" as text the row needed the short name and an icon Contact below 428px.
2. Chips above the ask box (this PR).
3. The existing two-row header.

The owner picked 2 with the envelope from 1, then asked for the chips in Chat view only and the envelope on desktop too.

## Risks / open items

- The phone preview's 360px caption no longer claims the name shortens there; a 280×653 Fold cover frame now shows the short name. At 280px the "Asking about" label is screen-reader-only so the System chip fits.
- Left for later (Opus review minors): leftover mobile-only classes on the desktop-only topic nav (harmless), and widening past md with a chip focused drops focus to the page (tablet rotation edge case). Both are in BACKLOG.
- The tooltip uses hover only on devices that can hover, so on phones it appears only with keyboard focus; the click result always shows.
