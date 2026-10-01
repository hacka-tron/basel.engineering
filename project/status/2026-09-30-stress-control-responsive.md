# Stress-test control: labelled button when there is room

**Status:** In review, PR [#56](https://github.com/hacka-tron/basel.engineering/pull/56) (branch `fix/stress-control-responsive`). Not merged.

## TL;DR
The footer's stress-test control now adapts to width. From 640px (`sm`) up there is a labelled "Stress test" button plus the tiger/rabbit icon beside it. Below 640px it is just the icon, as before. Only `frontend/src/components/StatsBar.tsx` changed.

## What changed for a visitor
- **Wide (>= 640px):** bordered "Stress test" button (shows "Stress test (8s)" during cooldown) runs the test. The icon next to it is details-only: hover or keyboard focus shows the capacity tooltip, a click toggles it. It does not run the test.
- **Phone (< 640px):** unchanged. The icon is the button: tap runs, press-and-hold (~500ms) shows details. The long-press state machine was not touched.
- **Padding:** the footer's left and right gutters are now `max(16px, safe-area inset)` below 640px, so the icon's visual edge sits 16px from the screen edge and its 44px hit area (6px from the edge) is not clipped. The owner's "too close to the corner" was likely a pinch-zoomed page, so spacing was kept to the standard gutter.

## How it works
The capacity tooltip ends with "Tap to run." on the icon-only layout and "Use the Stress test button to run it." from 640px up. The wide tooltip hides on outside pointerdown or after ~4s, and Escape dismisses either tooltip; CSS hover applies only on hover-capable devices (no sticky hover on iPad).

Both layouts are in the DOM and switched with CSS (`hidden sm:flex` / `sm:hidden`). `display:none` removes the hidden controls from tab order and the accessibility tree, so there is one control per action and no JS breakpoint. aria-labels: "Run stress test (real|simulated)" / "Stress test on cooldown, Ns remaining" on the runner, "Cluster capacity: ... Show details" on the wide icon.

## Decisions
- Breakpoint stays `sm`: at 414px the labelled button plus the stats text is too tight, and at 640px it fits with room (measured).
- Icon = details only when the labelled button is visible, to avoid two controls doing the same thing.
- The viewport meta has no `viewport-fit=cover`, so on iOS the safe-area insets resolve to 0 today; the `env()` terms are in place if that is ever added.

## Operational notes
Frontend only; no API or infra change. PR #54 also edits the footer's latency/cache text in the same file; this diff is confined to the stress-control area and the footer's padding classes.

## Verify
Headless Chrome at 360/375/414/640/768/1024/1440: correct control per width, no horizontal overflow (footer and document scrollWidth equal the viewport), tooltip on-screen on hover/focus/hold, label click runs the test, icon click at >= 640 does not, mobile tap and hold unchanged.

## Open items
None.
