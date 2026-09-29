# CLAUDE.md

Instructions for Claude Code when working in this repository.

## Repo layout

- `docs/` — design docs (`DESIGN.md` core architecture, `DESIGN-002-followups.md` resilience/chat features, `DESIGN-003-ingestion.md` content pipeline, `DESIGN-004-action-plan.md` build sequencing) and `docs/superpowers/plans/` implementation plans.
- `project/` — this file, `SNAPSHOT.md`, `BACKLOG.md` (see below). Kept out of the repo root to keep it readable; the root `CLAUDE.md` is a one-line stub that imports this file, so it still auto-loads.
- `services/` — application code.

Read the relevant design doc before implementing a feature. If an implementation needs to diverge from what's written, update the doc in the same change rather than letting it drift.

## Session memory (read this before scanning the repo)

- **`project/SNAPSHOT.md`** is the architecture/repo-state blueprint. Read it first when starting a session instead of scanning the repo tree.
- **`project/BACKLOG.md`** holds the `> RESUME HERE` pointer to the next action, open decisions only the owner can make, deferred milestones, bugs, and ideas.
- **When a task or phase completes**, update `project/SNAPSHOT.md` (what now exists), `project/BACKLOG.md` (move the resume point forward, log anything new), and this file if the working process itself changed. This is what keeps future sessions cheap — they orient from these three files instead of re-deriving context from the full repo and design docs every time.
- On long scoping/planning sessions, `/compact` periodically to keep context costs down.

## Commit discipline

Commit after each meaningfully complete unit of work (a build-plan phase, a feature, a fix) rather than batching unrelated changes together or leaving work uncommitted across sessions. Each commit should leave the repo in a working, reviewable state.

This matters even with an AI agent driving the work: commits are the checkpoints a human reviews, reverts, or bisects from if something turns out wrong. Since this project is explicitly built phase-by-phase (`docs/DESIGN.md` §17), frequent commits keep that phase structure visible in history instead of collapsing large spans of work into one diff.
