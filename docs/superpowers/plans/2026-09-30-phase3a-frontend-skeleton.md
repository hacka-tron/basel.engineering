# Phase 3a: Frontend Skeleton — Implementation Plan

> **For agentic workers:** Executed via the multi-model pipeline in `project/orchestration/` (Codex implements, Claude reviews — Gemini's quota is exhausted, see `project/BACKLOG.md`), same as Phase 1.

**Goal:** A static, visually-verifiable frontend skeleton matching `docs/DESIGN.md` §4's layout and visual rules — desktop and mobile — with no backend wiring yet. This is deliberately scoped short of full DD1 Phase 3 (no SSE streaming, no real chat, no citation popovers, no degraded-mode logic) so the visual design can be verified before the harder wiring work.

**Architecture:** Vite + React + TypeScript + Tailwind CSS. React Flow (`@xyflow/react`) for the architecture diagram. No backend calls at all in this phase — the chat panel and stats bar show static/placeholder content, and the diagram renders every node in idle state.

**Tech Stack:** Vite, React 19, TypeScript, Tailwind CSS (v4 — confirm installed major version and use its CSS-first `@theme` config, not a v3-style `tailwind.config.js`, if v4 installs), `@xyflow/react`, JetBrains Mono (self-hosted or via `@fontsource`, not a Google Fonts CDN link — no external network calls at runtime).

## Global Constraints

- **Repo layout** (`docs/DESIGN.md` §16): `frontend/src/architecture.ts` (node IDs/positions), `frontend/src/components/` (`Chat`, `ArchitecturePanel`, `CitationChip`, `StatsBar`, `StressTestButton`), `frontend/src/lib/sse.ts` (stub only in this phase — not implemented yet, just the file existing with a `// TODO: Phase 3b` comment is fine, or omit it entirely and note that in the report — your call).
- **Design tokens (exact, from the approved design plan):**
  - `--color-bg-base: #0B0D11`
  - `--color-bg-panel: #12151B`
  - `--color-border: #232830`
  - `--color-text-primary: #E6E9EE`
  - `--color-text-muted: #7C8494`
  - `--color-accent: #22D3EE` (active nodes/edges **only** — never decorative)
  - `--color-state-hit: #34D399` (cache hit)
  - `--color-state-miss: #F5A623` (cache miss)
  - Idle node color: use `--color-border`/`--color-text-muted`, not a separate token.
  - Font: JetBrains Mono, one family for everything (headers, chat prose, data, labels) — no second family.
  - No card shadows, no border-radius beyond a small 2-4px on interactive elements (input, chips, buttons) — panels themselves are flat rectangles separated by hairline `--color-border` lines.
  - Footer stats bar uses a thin vertical rule (`│` character or a 1px border) between stats, not a middle-dot (`·`) separator.
- **Visual rules from `docs/DESIGN.md` §4.1:** only the diagram animates (not applicable yet in this phase since nothing is wired/live — but don't add gratuitous entrance animations, hover-fades-on-everything, etc. to the static skeleton; keep it visually calm per the same principle).
- **No placeholder Lorem Ipsum.** Use the real suggested-question chip copy from `docs/DESIGN.md` §4.3 (reproduced in Task 2 below) and real `NodeId` values from §8 (reproduced in Task 3 below) — this is a skeleton of the real thing, not a generic mockup.
- Commit after each task.

---

### Task 1: Vite scaffold, design tokens, static desktop layout shell

**Files:**
- Create: `frontend/` (via `npm create vite@latest frontend -- --template react-ts`, then add Tailwind)
- Create: `frontend/src/index.css` (or `app.css`) — Tailwind entry + `@theme`/CSS custom properties for the design tokens above
- Create: `frontend/src/components/Chat.tsx` — chat panel shell: suggested-question chips, an input box with a send button, no message list logic yet (static empty state)
- Create: `frontend/src/components/StatsBar.tsx` — footer bar with placeholder static numbers (`p50 —ms`, `cache hit —%`, `— queries served`) and a "Stress test" button (visually present, no click behavior yet — can be disabled/inert)
- Create: `frontend/src/App.tsx` — assembles the header, chat panel, a placeholder box where the architecture panel will go (Task 2), and the stats bar, matching the desktop layout in `docs/DESIGN.md` §4.1 (chat ~40% / architecture ~60%, single screen, no page scroll)
- Modify: `frontend/index.html` — page title ("Basel Abdel-Rahman" or similar, your call on exact wording, keep it simple), font loading (self-hosted, see below)

**Spec:**

- **Header:** site owner name/title on the left, a two-option toggle in the center/right ("About Basel" / "About This System" — per §4.3, static/inert for now, but visually show which is "selected" via the accent color on the active option — this is a legitimate, non-decorative use of the accent since it IS an active/selected state), a GitHub link on the far right (use `https://github.com/hacka-tron` as the href — this is the real GitHub URL from the corpus content, `corpus/about-me/bio.md`).
- **Corpus toggle chips**, exact copy from `docs/DESIGN.md` §4.3 — show whichever corpus is "selected" (default to "About Basel"):
  - About Basel: "What has Basel built with distributed systems?", "What did Basel work on at YouTube?", "Is Basel a fit for a platform engineering role?"
  - About This System: "How does the caching work?", "Why k3s instead of EKS?", "What happens when I press stress test?", "Show me the Terraform for the database."
  - Clicking a chip should populate the input box with that question's text (real interactivity, doesn't need to submit anywhere yet — there's nothing to submit to in this phase).
- **Input box:** placeholder text "Ask anything...", a send button/icon. Enter key and clicking send can both just be no-ops for now (or log to console) — no backend call yet.
- **Font loading:** self-host JetBrains Mono (e.g. `npm install @fontsource/jetbrains-mono` and import the weights you need — 400, 500, 600) rather than a CDN `<link>` tag, so the page has no external network dependency at runtime.
- **No React Flow yet** — Task 2 owns the actual diagram. Leave a simple placeholder `<div>` in `App.tsx` where it will go (e.g. with a border and "architecture panel" label), so Task 1's layout can be verified independently.

**Verification:** `npm run dev`, confirm the page renders with correct colors/font/layout at a normal desktop width (the orchestrator will do a real visual check — you don't need a screenshot tool, just confirm the dev server starts cleanly and `npm run build` succeeds with no errors).

- [ ] Dispatch to Codex.
- [ ] Orchestrator: run `npm run dev`, verify it starts; run `npm run build`, verify it succeeds; visually inspect (screenshot or direct viewing).
- [ ] Dispatch to Claude for review.
- [ ] Commit.

---

### Task 2: Architecture panel (React Flow, idle state)

**Files:**
- Create: `frontend/src/architecture.ts` — node IDs, labels, and layout positions
- Create: `frontend/src/components/ArchitecturePanel.tsx`
- Modify: `frontend/src/App.tsx` — replace Task 1's placeholder with the real panel

**Spec:**

- `architecture.ts` exports the node list matching the exact `NodeId` union from `docs/DESIGN.md` §8: `"edge" | "api" | "answer_cache" | "queue" | "worker" | "embed_cache" | "embed" | "vector_search" | "mysql" | "llm"`. Each node needs an `id` (the `NodeId`), a display `label` (a short human-readable name — e.g. `edge` → "Edge", `answer_cache` → "Answer Cache", `vector_search` → "Vector Search"), and an `x`/`y` position for React Flow's layout. Lay them out to roughly match `docs/DESIGN.md` §5.1's architecture diagram flow: `edge → api → answer_cache`, then down to `queue → worker`, then `embed_cache → embed → vector_search → mysql` (parallel-ish branch off the worker), then `llm`. Use your judgment on exact coordinates for a clean, readable layout — doesn't need to be pixel-identical to any particular diagram, just structurally sensible and non-overlapping.
- Also define the edges (connections) between nodes matching that same flow.
- `ArchitecturePanel.tsx` renders this via `@xyflow/react`'s `<ReactFlow>` with custom node components styled per the design tokens: idle nodes use `--color-border` for the outline and `--color-text-muted` for the label text, on the `--color-bg-panel` background. No live data yet — every node renders in idle state (this phase doesn't wire up trace events).
- Below the diagram, a static "Retrieved chunks" section header with placeholder/empty state text (e.g. "No query yet" — real empty-state copy, not "Lorem ipsum") — this is where retrieved chunk results will render once wired up in a later phase.
- Disable React Flow's default interactive background pattern/controls that don't fit the visual rules if they clash (e.g. the default dotted background) — use your judgment for what looks clean against the dark theme, but keep it minimal (no gradient washes).

**Verification:** the diagram renders with all 10 nodes visible, correctly connected, no overlapping labels, matching the idle-state color scheme.

- [ ] Dispatch to Codex.
- [ ] Orchestrator: visual verification (screenshot).
- [ ] Dispatch to Claude for review.
- [ ] Commit.

---

### Task 3: Mobile responsive layout

**Files:**
- Modify: `frontend/src/App.tsx`, `frontend/src/components/ArchitecturePanel.tsx`, `frontend/src/index.css` (or wherever breakpoint logic lives)
- Create: `frontend/src/components/PipelineStrip.tsx` (mobile-only collapsed diagram view)

**Spec (`docs/DESIGN.md` §4.2):**

- Below a reasonable mobile breakpoint (e.g. Tailwind's default `md` at 768px — your call, note what you picked), the chat takes full width.
- The full architecture diagram is replaced by a horizontal "pipeline strip" — small labeled dots for each node in a horizontal scrollable row above the chat input, in idle state (all gray/muted, since nothing is live in this phase).
- A "View architecture" button/link opens the full `ArchitecturePanel` diagram as a bottom sheet (a simple slide-up overlay is fine — doesn't need a heavy modal library, a basic fixed-position panel with a close button is acceptable given this phase has no interactivity to preserve underneath it).
- Verify the desktop layout (Task 1/2) is unaffected above the breakpoint.

**Verification:** resize/view at a mobile viewport width (e.g. 375px), confirm the pipeline strip renders instead of the full diagram, and the "View architecture" sheet opens/closes correctly.

- [ ] Dispatch to Codex.
- [ ] Orchestrator: visual verification at mobile width (screenshot).
- [ ] Dispatch to Claude for review.
- [ ] Commit.

---

## Self-Review Notes

- **Spec coverage:** Covers the layout, visual design, and structural skeleton from `docs/DESIGN.md` §4.1–§4.3 and the repo layout from §16, deliberately excluding SSE wiring, real chat, citations, and degraded modes (a later "Phase 3b" once this skeleton is visually approved).
- **Placeholder scan:** No Lorem Ipsum — all copy is real content from the design doc or plainly-labeled real empty states.
- **Type/interface consistency:** `architecture.ts`'s `NodeId` values match `docs/DESIGN.md` §8's `NodeId` type exactly, so Phase 3b's SSE wiring can map trace events to these same node IDs without translation.
- **Scope check:** Three tasks, ending in a fully responsive, visually-verified static skeleton — no backend dependency, so it can be verified and approved independently of the wiring work.
