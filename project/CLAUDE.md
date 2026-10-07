# CLAUDE.md

Instructions for Claude Code when working in this repository.

## Repo layout

- `docs/` — design docs (`DESIGN.md` core architecture, `DESIGN-002-followups.md` resilience/chat features, `DESIGN-003-ingestion.md` content pipeline, `DESIGN-004-action-plan.md` build sequencing, `DESIGN-005-rag-quality.md` RAG quality) and `docs/superpowers/plans/` implementation plans.
- `project/` — this file, `SNAPSHOT.md`, `BACKLOG.md` (see below), `status/` (owner-facing feature reports, see below), `PORTFOLIO_PROJECT.md` (playbook for adding a portfolio project), `MOBILE_DESIGN.md` (responsive/typography rules and the screenshot checklist — required reading before any `frontend/` change), `orchestration/` (model roles, the review gate, merging; use its templates instead of writing dispatch prompts from scratch) and `archive/` (old handoff checkpoints, the 09-29/30 pilot notes and legacy Codex/Gemini guides). Kept out of the repo root; the root `CLAUDE.md` is a stub that imports this file. Not ingested into the chatbot corpus (only `docs/`, `infra/`, `k8s/`, `services/` and the About Basel files are).
- `services/` — application code.

Read the relevant design doc before implementing a feature. If an implementation needs to diverge from what's written, update the doc in the same change rather than letting it drift.

## Working style (owner preferences)

Details and the why are in `orchestration/README.md`.

- **Orchestrate, parallelize.** Claude orchestrates with parallel worktree subagents (`.worktrees/<name>`): Opus for judgment and review, Sonnet for mechanical work and verification loops. Scope new features with a subagent, not in the main conversation. When work waits on review or the owner, start the next backlog item.
- **Track expensive work.** Log costly loops and workflows in `orchestration/token-log.md` with the fix, and turn repeated work into a brief, playbook or script (`orchestration/README.md`).
- **Review gate:** an Opus subagent with `orchestration/reviewer-brief.md` (reads `orchestration/reviewer-primer.md` first). Codex is optional, only when it has usage; Gemini is legacy. **Round cap 2:** after that only Critical findings, or Important ones with a reachable failure scenario, block; the rest go to `BACKLOG.md`. Track each open PR's round, verdict and fixes since.
- **PRs:** record the review in the PR body with `gh api -X PATCH` (`gh pr edit` fails here). Chain or stack PRs that touch the same files, and trial-merge parallel ones.
- **Merge without asking** once review is APPROVED and CI is green: `gh pr update-branch`, full test suite on the merged tree, wait for green CI, then `gh pr merge` as its own command. Exception: changes needing the owner's go-ahead (live infra, IAM, RBAC; `orchestration/README.md` "What checked in means" step 6).
- **Process-doc changes just check in.** A PR touching only `project/**` (except `SNAPSHOT.md`, which a test reads) or repo-root `*.md` (exclusively; renames or moves from other directories count as code) skips the test jobs (CI's `changes` gate, `.github/scripts/ci-code-changed.sh`) and needs no review round: open it and merge once the skipped checks report. `docs/**` is chatbot corpus and keeps full CI.
- **The owner never runs AWS or Terraform by hand.** Production changes go through the Terraform, Bootstrap and "Ops · ..." workflows (`infra/CI.md`); ask the owner for approval clicks, never commands. **Every dispatch restates the hard rules** (the canonical list is in `orchestration/README.md` "Working rules"; copy it, don't paraphrase it).
- **Owner content rules:** RAG evaluations are approved as of 2026-10-03 (the owner added documents); any RAG-plan task reads `orchestration/rag-plan-brief.md` first (owner decisions, environment, spend limits, gotchas). The About Basel files mirror the owner's resume: don't edit them to fix architecture facts (raise it with the owner). About Basel content lives only in the private repo `hacka-tron/basel.engineering-docs`; there is no public About Basel text in this repo, and none may be added.
- **Adding a portfolio project** (a common owner request): follow `PORTFOLIO_PROJECT.md`; don't explore the portfolio code.
- **Mobile testing:** use the phone preview (`cd frontend && npm run phone`, see `MOBILE_DESIGN.md`) and side-by-side pages when the owner chooses between designs. Record each mobile design decision in `MOBILE_DESIGN.md`.
- **Status reports** for every substantial change (below), and add generalizable lessons to `~/Coding/template` as you go, in the guide they belong to.

## Session memory (read this before scanning the repo)

- **`project/SNAPSHOT.md`** is the architecture/repo-state blueprint. Read it first when starting a session instead of scanning the repo tree.
- **`project/BACKLOG.md`** holds the `> RESUME HERE` pointer to the next action, open decisions only the owner can make, deferred milestones, bugs, and ideas.
- **When a task or phase completes**, update `project/SNAPSHOT.md` (what now exists), `project/BACKLOG.md` (move the resume point forward, log anything new), and this file if the working process itself changed. This is what keeps future sessions cheap — they orient from these three files instead of re-deriving context from the full repo and design docs every time.
- On long scoping/planning sessions, `/compact` periodically to keep context costs down.

## Status reports (owner preference)

After each substantial feature or change, write or update a report in `project/status/` (`YYYY-MM-DD-HHMM-<slug>.md`, Pacific time first written; the H1 ends with `(YYYY-MM-DD HH:MM PT)`) and add it to the index in `project/status/README.md`. Write it when the PR opens, then update its status at merge and again at deploy. The reader is the owner, a technical boss who wants a strong grasp of the system: TL;DR, visitor-visible change, architecture with a small Mermaid diagram, design decisions and why, what review caught, operational risks, how to verify, open items. `project/status/README.md` has the format. Delegating the writing to a subagent is fine. Never put account IDs, IPs or tokens in a report.

## Commit discipline

Commit after each meaningfully complete unit of work (a build-plan phase, a feature, a fix) rather than batching unrelated changes together or leaving work uncommitted across sessions. Each commit should leave the repo in a working, reviewable state.

This matters even with an AI agent driving the work: commits are the checkpoints a human reviews, reverts, or bisects from if something turns out wrong. Since this project is explicitly built phase-by-phase (`docs/DESIGN.md` §17), frequent commits keep that phase structure visible in history instead of collapsing large spans of work into one diff.
