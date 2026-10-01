# Mobile header: one row, topic chips above the ask box

**Status:** In review, PR [#78](https://github.com/hacka-tron/basel.engineering/pull/78) · **Scope:** `frontend/` below 768px only; desktop pixel-identical.

## TL;DR

The phone header is now a single 61px row (name, Contact me, GitHub) in both Chat and Diagram views. The question topic moved out of the header into a row of two chips directly above the ask box: "Asking about (● Basel) (○ System)". The topic dropdown in the header row was built and measured first, but it failed the owner's fit rule, so per the owner's decision rule this ships option 4 (chips) instead. The dropdown attempt is the first commit on the branch for reference.

## Why not the dropdown

Owner's rule: at 375px and wider the header row may use at most one fallback (short name or an icon-only Contact, not both), with 16px gaps and 44px targets.

Measured in headless Chrome with the real fonts (row = name, "About Basel ▾" menu button at 14px, Contact, GitHub):

| Width | Steps needed |
|---|---|
| ≥ 519px (shrinking) / 528px (growing) | none: full name + "Contact me" |
| 428–518px / 438–527px | short name "Basel A-R" + "Contact me" |
| < 428px / < 438px | short name and envelope icon (both fallbacks) |

So every common phone (360, 375, 390, 414) needed both fallbacks. At 375px the one-fallback layout is 53px short. A shorter button label ("Basel ▾") saves about 50px, still not enough, and loses clarity. Screenshots of the attempt, with the menu open, are in the session scratchpad (`dropdown-<width>-<view>[-menu-open].png`). The menu itself worked: 44px items, checkmark on the current topic, on screen at 320px (x 83–275).

## What changed for a visitor (phones)

- Header: one row, 8px padding above and below (61px), the same in Chat and Diagram. The full name shows from 376px (it stays down to 366px when shrinking); below that it reads "Basel A-R". Contact keeps its text label at every width.
- Topic: chips directly above the ask box in both views. Tapping one switches the topic exactly as the old nav did (same handler: one conversation per topic, component selection cleared when going to About Basel). The topic is now always visible next to where the question is typed, including in Diagram view, where it used to be hidden.
- Removed: the Diagram-view collapse of the old nav row, its focus rescue, and the animated bottom padding.

## How it works

```
header  [Basel Abdel-Rahman ........ Contact me (gh)]   61px, py-2, flex-nowrap
main    messages / diagram
        PipelineStrip  [stages ...      Chat|Diagram]
        input box  [Asking about (● Basel) (○ System)]  44px, replaces the 8px top padding
                   [Ask anything...              ][→]
footer  stats ... + (bunny)
```

- `components/TopicChips.tsx`: `role="radiogroup"` labelled "Asking about", buttons with `role="radio"` and `aria-checked`, roving tabindex, arrows/Home/End move and select (wrapping). Visible labels "Basel"/"System"; accessible names are the full topic names (contain the visible text). Chip text 14px, label 12px, each target 44px tall.
- `Chat.tsx` gains an `inputTopic` slot rendered above the form, mobile only; on desktop nothing is passed, so the container keeps its `py-2`.
- `App.tsx`: one `selectTopic` handler for desktop nav and chips. The header is `flex-nowrap` with `py-2`; the Contact/GitHub group is `max-md:shrink-0` so a long name is shortened by `useFullNameFits` rather than squeezing Contact (with `nowrap`, it used to get squeezed: the measured width shrank and the full name stayed too long).

## Measurements (viewport height 740; origin/main vs this branch)

| Width | Header (main → branch) | Name | Name–Contact gap | Chat message area | Diagram area |
|---|---|---|---|---|---|
| 320 | 97 → 61 | Basel A-R | 54 | 472 → 472 (0) | 508 → 472 (−36) |
| 360 | 97 → 61 | Basel A-R | 93 | 472 → 472 (0) | 508 → 472 (−36) |
| 375 | 97 → 61 | Basel Abdel-Rahman | 24 | 472 → 472 (0) | 508 → 472 (−36) |
| 390 | 97 → 61 | Basel Abdel-Rahman | 38 | 472 → 472 (0) | 508 → 472 (−36) |
| 414 | 97 → 61 | Basel Abdel-Rahman | 61 | 472 → 472 (0) | 508 → 472 (−36) |

One row at every width 320–767 (row items' centres within 1px), no horizontal scroll. At 375×667 the same deltas hold (chat 399 → 399, diagram 435 → 399).

The chip row costs 36px net (44px row minus the 8px padding it replaces). In Chat that is exactly the 36px the header gave back. In Diagram view the old header was already 61px, so the diagram is 36px shorter than on main. That is the price of showing the topic in Diagram view, which the owner asked for.

## Verified

- `npm run lint`, `tsc -b`, `npm test` (58 pass), `npm run build`.
- Topic switching: chip "About This System" then a question sent `corpus: about_system`; back to "About Basel" sent `about_me`; in Diagram view the chip switched and the ask sent `about_system`. Suggested questions switch with the topic.
- Keyboard: Tab order is Contact, GitHub, suggestions, pipeline, Chat, Diagram, checked chip, ask box. ArrowRight selects System, wraps back to Basel; End/Home work; Tab leaves the group to the ask box.
- Focus mode: with the ask box focused (header and footer hidden), tapping a chip switches the topic.
- Desktop: screenshots at 768×1024, 1024×900 and 1440×900 of origin/main and this branch (both production builds) differ in 0 pixels.

## Risks / open items

- Diagram view has 36px less height than on main. If that matters, the chips could share the PipelineStrip row in Diagram view, where it holds only a status line.
- The dropdown attempt's code (row-one measured fallbacks, envelope Contact with a toast) is in the branch history (first commit) if a wider-screen variant is wanted later.
