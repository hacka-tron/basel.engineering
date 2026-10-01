# CLAUDE.md

Instructions for Claude Code when working in this repository.

## Repo layout

- `docs/` — design docs (`DESIGN.md` core architecture, `DESIGN-002-followups.md` resilience/chat features, `DESIGN-003-ingestion.md` content pipeline, `DESIGN-004-action-plan.md` build sequencing) and `docs/superpowers/plans/` implementation plans.
- `project/` — this file, `SNAPSHOT.md`, `BACKLOG.md` (see below), `status/` (owner-facing feature reports, see below), `MOBILE_DESIGN.md` (responsive/typography rules and the screenshot verification checklist — required reading before any `frontend/` change), and `orchestration/` (model roles and the Codex review-gate template — Claude orchestrates and implements, choosing Opus/Sonnet/Haiku subagents per task and parallelizing across worktrees; Codex reviews and validates every change before check-in. Use the templates instead of writing dispatch prompts from scratch). Kept out of the repo root to keep it readable; the root `CLAUDE.md` is a one-line stub that imports this file, so it still auto-loads.
- `services/` — application code.

Read the relevant design doc before implementing a feature. If an implementation needs to diverge from what's written, update the doc in the same change rather than letting it drift.

## Working style (owner preferences)

- Scope new features with a subagent (see `orchestration/README.md`), not in the main conversation, so the orchestrator's context stays short.
- When current work is waiting on review or owner approval, pick up the next backlog item instead of idling.
- **Merge without asking** once a PR's review is APPROVED and CI is green: run `gh pr merge` as its own command (not chained with other commands). Exception: changes that need the owner's explicit go-ahead (see `orchestration/README.md` "What checked in means", step 6).
- **Reviewer:** Codex by default. When Codex is out of usage, an Opus subagent reviews with the same brief (`orchestration/codex-reviewer.md` prompt, `orchestration/reviewer-primer.md` first). **Round cap:** after 2 review rounds, only Critical findings or Important findings with a reachable failure scenario block the merge; everything else goes to `BACKLOG.md`. Keep track of each open PR's review state (round, verdict, what is fixed).
- **The owner never runs AWS or Terraform by hand.** Production changes go through the Terraform, Bootstrap and "Ops · ..." workflows (`infra/CI.md`); ask the owner for approval clicks, never for commands to paste. Every infra dispatch (implementer or reviewer) restates the hard rules: never read, open or copy any `terraform.tfstate` or plan file; no `terraform apply` and no live AWS/Kubernetes writes from agents.
- **Mobile testing:** use the phone preview (`cd frontend && npm run phone`, see `MOBILE_DESIGN.md`) and side-by-side comparison pages when the owner has to choose between designs, not a shrunk browser window. Record each mobile design decision in `MOBILE_DESIGN.md`.
- **Status reports** for every substantial change (section below), and add generalizable lessons to the `~/Coding/template` repo as you go, in the guide they belong to.

## Session memory (read this before scanning the repo)

- **`project/SNAPSHOT.md`** is the architecture/repo-state blueprint. Read it first when starting a session instead of scanning the repo tree.
- **`project/BACKLOG.md`** holds the `> RESUME HERE` pointer to the next action, open decisions only the owner can make, deferred milestones, bugs, and ideas.
- **When a task or phase completes**, update `project/SNAPSHOT.md` (what now exists), `project/BACKLOG.md` (move the resume point forward, log anything new), and this file if the working process itself changed. This is what keeps future sessions cheap — they orient from these three files instead of re-deriving context from the full repo and design docs every time.
- On long scoping/planning sessions, `/compact` periodically to keep context costs down.

## Status reports (owner preference)

After each substantial feature or change, write or update a report in `project/status/` (`YYYY-MM-DD-<slug>.md`) and add it to the index in `project/status/README.md`. Write it when the PR opens, then update its status at merge and again at deploy. The reader is the owner, a technical boss who wants a strong grasp of the system: TL;DR, visitor-visible change, architecture with a small Mermaid diagram, design decisions and why, what review caught, operational risks, how to verify, open items. `project/status/README.md` has the format. Delegating the writing to a subagent is fine. Never put account IDs, IPs or tokens in a report.

## Commit discipline

Commit after each meaningfully complete unit of work (a build-plan phase, a feature, a fix) rather than batching unrelated changes together or leaving work uncommitted across sessions. Each commit should leave the repo in a working, reviewable state.

This matters even with an AI agent driving the work: commits are the checkpoints a human reviews, reverts, or bisects from if something turns out wrong. Since this project is explicitly built phase-by-phase (`docs/DESIGN.md` §17), frequent commits keep that phase structure visible in history instead of collapsing large spans of work into one diff.
