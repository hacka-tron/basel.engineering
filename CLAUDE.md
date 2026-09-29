# CLAUDE.md

Instructions for Claude Code when working in this repository.

## Design docs

The system design lives in `DESIGN.md` (core architecture), `DESIGN-002-followups.md` (resilience/chat features), `DESIGN-003-ingestion.md` (content pipeline), and `DESIGN-004-action-plan.md` (build sequencing and setup checklist). Read the relevant doc before implementing a feature. If an implementation needs to diverge from what's written, update the doc in the same change rather than letting it drift.

## Commit discipline

Commit after each meaningfully complete unit of work (a build-plan phase, a feature, a fix) rather than batching unrelated changes together or leaving work uncommitted across sessions. Each commit should leave the repo in a working, reviewable state.

This matters even with an AI agent driving the work: commits are the checkpoints a human reviews, reverts, or bisects from if something turns out wrong. Since this project is explicitly built phase-by-phase (`DESIGN.md` §17), frequent commits keep that phase structure visible in history instead of collapsing large spans of work into one diff.
