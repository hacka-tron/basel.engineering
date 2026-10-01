# Diagram: deselect by empty space, the details chevron, or Escape

**Status:** In review, PR [#114](https://github.com/hacka-tron/basel.engineering/pull/114). Not merged.

## TL;DR

Clicking or tapping empty space in the diagram now deselects the selected component, on desktop and on phones. On phones, the chevron on the open details panel (the "dropdown from the description") now deselects too, instead of collapsing the panel while the component stays selected. Escape also deselects; in the phone Diagram view the first Escape deselects and the next one returns to Chat. An answer that is still streaming is never stopped: it keeps streaming into Chat and its stages keep lighting up.

Owner's request (2026-10-01): "can you make it when i click on an empty space in the diagram section, it unselects my component. on mobile, I want it so that if i click the dropdown from the description, it also follows that same behavior".

## What changed for a visitor

- **Desktop:** select a node, click empty diagram space: the node's border goes away and the inspector below shows "Hover or focus a component to see what runs it." Dragging the diagram to pan keeps the selection. Escape deselects too.
- **Phone (portrait and landscape):** tap a node and the details panel opens, as before. Tap empty space, or the chevron in the panel's top-right corner, and the panel goes back to the locked "Select a component for details" bar. Panning the diagram with a finger keeps the selection.
- **Gone:** the collapsed "Worker details · 8 chunks" bar. The panel is now open exactly while a component is selected.

## How it works

```mermaid
flowchart LR
    P["empty-space click/tap<br/>(React Flow onPaneClick)"] --> D[deselect]
    C["details chevron<br/>(phones)"] --> D
    E["Escape<br/>(capture phase, preventDefault)"] --> D
    D --> A["App: selectedNode = null<br/>queued component question dropped"]
    A --> I["desktop: inspector empty state"]
    A --> L["phones: locked bar<br/>(focus moves there if the panel held it)"]
    S["streaming request"] -.->|unaffected| H["activeNode highlight + Chat answer"]
```

- `ArchitecturePanel` gets an `onDeselect` prop. React Flow's `onPaneClick` only fires when the pane itself is the click target (nodes and arrows are excluded), and React Flow suppresses the click after a mouse drag; on touch, a pan is not a tap, so no click is generated.
- The phone panel's state is now just "selected or not". The `detailsOpen` state, the expand/collapse toggle, the collapsed bar and `portraitDetailsState` are removed; `lib/detailsPanel.ts` keeps the hint text and gains `deselectsOnKey` (tested).
- Escape is handled by a document listener in the capture phase that marks the event handled; the phone Diagram view's Escape-to-Chat listener already skips handled events, so the order is fixed without touching it.

## Key design decisions and trade-offs

- **Streaming answers keep going.** Deselecting clears only the selection. The cyan "running" tile follows the request (`activeNode`), not the selection, so stage highlighting is unaffected. A component question queued behind another answer is dropped, because the visitor just let go of it.
- **No duplicate question on re-select.** Tapping the same component again while its answer still streams (likely after a deselect) shows that answer again instead of queueing the same question a second time (new `inFlightQuestionRef` in `App.tsx`). The same holds once the answer has finished, as long as it is still the latest About This System question (`latestQuestionAnswered`); a failed or stopped answer is asked again.
- **Tooltips take Escape first.** An Escape that closes a showing footer tooltip does only that; the next Escape deselects, and the one after returns to Chat on phones.
- **Escape order:** first Escape deselects, the next returns to Chat (phones). This works from the ask box too. On desktop, Escape anywhere deselects (there was no other Escape meaning there).
- **Focus:** the chevron, or an Escape while focus is inside the panel, moves focus to the locked bar, never to `<body>`. A tap on empty space leaves focus where the tap put it, as any click on a non-focusable area does.
- **Chevron semantics:** it now reads "Close <Component> details and deselect it" and has no `aria-expanded`, since nothing can reopen the panel except selecting a component. It keeps its 44px target. Node buttons' `aria-pressed` follows the selection as before.
- **Desktop hover preview** is cleared on deselect, so a node that still had keyboard focus doesn't keep filling the inspector.

## What review caught

Opus review, round 1: CHANGES NEEDED. The deselect logic, the Escape order, the `inFlightQuestionRef` lifecycle and the interaction with #113's landscape zoom squeeze were confirmed sound. Fixed in round 1:

- **Important: re-selecting a component after its answer finished asked again.** The common path (tap Queue, read it, tap the chevron, tap Queue again) appended a duplicate Q&A and spent one of the visitor's 10 questions per 10 minutes, because the rate limiter runs before the answer cache. Now, when that component's question is still the latest About This System question and its reply settled normally (`done` or `retrieval_only`), selecting it just shows that reply. A failed or stopped reply is asked again as before (Retry also exists). Pure helper `latestQuestionAnswered` in `lib/conversation.ts`, unit-tested.
- **Important: merge conflict with `main`** in the status index; both rows kept.
- **Minor: an Escape that closed a footer tooltip also deselected.** The tooltip Escape handlers (`useLongPressTooltip`, the wide stress-test tooltip in `StatsBar`) now listen in the window capture phase, which runs before the document-level deselect listener, and call `preventDefault` when a tooltip was actually showing (press/long-press open, or keyboard focus showing the CSS tooltip). The deselect and back-to-Chat listeners skip handled events. Order documented in `lib/escapeKey.ts` (`tooltipShowing`, unit-tested).
- **Minor:** Escape with `keyCode` 229 (Safari's IME composition) is ignored.
- **Minor:** `MOBILE_DESIGN.md` wording: the open panel's chevron (the locked bar's chevron does nothing), "close (deselect) button" instead of "collapse button", and "locked bar or the details open" instead of "collapsed or open".

## Operational notes and risks

Frontend only; no API or infra change. Low risk. The behaviour change is intentional: the collapse-while-selected state from #90 no longer exists.

## How to see it / verify it

- `cd frontend && npm test && npm run lint && npm run build` (106 tests; `detailsPanel.test.ts` covers `deselectsOnKey`, `conversation.test.ts` covers `latestQuestionAnswered`, `escapeKey.test.ts` covers `tooltipShowing`).
- Headless Chrome against the phone preview server (`npx vite --config vite.phone.config.ts --port 5242`), with `/api/ask` faked in the page (a slow canned stream; 0 live questions), real CDP mouse and touch input:
  - 1280x800: select Queue, click empty pane: deselected, inspector empty state, the answer keeps streaming (chat text grew, LLM tile still cyan). Re-select while streaming: no second request. Drag the pane (the viewport moved): still selected. Escape: deselected.
  - 393x852 and 667x375: tap Queue: panel open. Tap empty space: locked bar. Finger pan (viewport moved): still selected, panel open. Chevron (44x44): deselected, locked bar (44px tall) has focus. Select MySQL, focus the chevron, Escape: deselected, still in Diagram view, focus on the locked bar; second Escape: back to Chat with focus on the Diagram toggle. No horizontal scroll.

- Round 1 fixes, same harness: at 1280x800, 393x852 and 667x375, once Queue's answer has finished, deselecting it (empty pane or chevron) and selecting it again makes no new request, and on phones the panel shows the finished answer. A different component still asks. With a footer tooltip showing (desktop: keyboard focus on the stress-test details icon; phones: a long press on "+"), Escape closes only the tooltip and keeps the selection and the Diagram view, and the next Escape deselects. The round-0 checks were re-run and still pass.

## Open items

- Real-device touch check on iOS Safari (the touch path was verified with Chrome's touch emulation only).
