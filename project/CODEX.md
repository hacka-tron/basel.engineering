# Codex guide

This guide applies when Codex is implementing or coordinating. `project/AGENT_HANDOFF.md` names the current project lead and executor. Claude's original operating guide remains in `project/CLAUDE.md` and `project/orchestration/README.md`.

## Start a session

1. Read `project/AGENT_HANDOFF.md` for the current coordinator and exact checkpoint.
2. Read `project/SNAPSHOT.md` for architecture and repo state, `project/BACKLOG.md` for the next action, and the relevant design doc before implementation.
3. Inspect the current branch, `git status --short`, recent commits, and worktrees. Understand another agent's uncommitted work before changing it.

## Coordinate and implement

Codex may write code, run services and tests, and commit working units when the user has asked it to execute the plan. Claude is currently the project lead and will evaluate Codex's work. Use available tools and the current environment; do not assume the older Codex CLI sandbox is in effect. Preserve the project's provider boundaries so local, AWS, and future cloud implementations can be swapped at the documented seams.

Follow the owner's role split when Claude is out of tokens: Claude's design docs remain the architecture; Codex implements the current request; Gemini, through `agy`, validates spec compliance and code quality using `project/orchestration/gemini-reviewer.md` as the review brief. Resolve material findings and verify the running system before reporting completion. If Gemini is unavailable or out of quota, record that plainly in `project/AGENT_HANDOFF.md` and ask Claude to review when it resumes; do not imply Gemini approved the change. Do not alter Claude's orchestration guide to describe the Codex-led run.

After a meaningful unit, update `project/SNAPSHOT.md` with verified state, `project/BACKLOG.md` with the next resume point, and `project/AGENT_HANDOFF.md` with exact test evidence and unresolved blockers. Commit a working, reviewable unit. If implementation changes the architecture, update the relevant design doc in the same change.

### Work in a worktree, not directly on `main`, for anything beyond a one-commit fix

Every other phase in this project (0, 1a, 1b, 3a, 3b) used `.worktrees/<phase-name>`, landing on `main` only via an explicit merge after review. The Phase 2 run (providers, three cache layers, rate limiting, eval — ~2,400 lines across 7 commits) skipped this and committed straight to `main`, because the coordinator switch to Codex didn't re-state the rule. The result: a full phase of security- and cost-relevant code (rate limits, a daily spend cap, cache correctness) sat on `main`, unreviewed by anyone, until the owner asked for a review well after the fact. The review found the atomicity/concurrency work was done correctly, but also one Critical latent bug (the live retrieval index has no embedding-model isolation — a partial re-ingestion failure can silently leave a stale-model vector mixed into live KNN search results) that a pre-merge review would have caught before it could affect a real answer.

**Going forward:** a single trivial fix (one file, no new tested behavior) may still commit directly to `main`. Anything larger — a plan with multiple tasks, new cache/limit/provider logic, anything touching cost or correctness — works in `.worktrees/<phase-name>` on its own branch. Merge to `main` only after Claude reviews it, or, if Claude is unavailable, after a Gemini review via `agy` **and** the owner's explicit go-ahead to merge without Claude's review. Record whichever review actually happened (or didn't) in `project/AGENT_HANDOFF.md` exactly as already practiced — that part worked well and should continue unchanged: the handoff notes from this run were detailed and, on independent verification, accurate.

### Validate configuration at startup, not on first request

If a plan says "validate at startup," add an actual FastAPI startup/lifespan hook that constructs and sanity-checks the configured providers (and anything else config-driven), not just a lazy check that happens to raise on first use. A malformed `GLASSBOX_PROVIDER` or model ID should fail the health check, not fail in front of the first real user.

### Treat partial failure in any multi-step operation that touches shared state as blocking, not soft-fail-and-continue

Per-document exception handling in ingestion (added in Phase 1a, intentionally, so one bad document doesn't fail an entire ingestion run) is correct for isolated per-document data. It is **not** correct when a step also writes into shared state one document's failure can silently corrupt for every other document — e.g. a shared vector index where "corpus X's index only ever contains model Y's vectors" is an invariant nothing enforces. When adding a step like that, either enforce the invariant explicitly (tag records with the model/version that produced them and filter reads by it, the way the answer cache already correctly does) or make the whole operation fail closed rather than leaving mixed-model data live and queryable.

### Real paid API calls: a small standing allowance, not case-by-case judgment

Codex judged, correctly as it turned out, that a direct user report of a broken canned answer implied authorization for a handful of small paid Bedrock verification calls. That was a reasonable one-off call, but shouldn't need to be re-decided from scratch each time. Standing rule: up to roughly 10 small/low-token verification calls (single questions, short `maxTokens`) don't need to be asked about first — record them in `project/AGENT_HANDOFF.md` same as everything else. A full corpus re-embed, a bulk eval run against real Bedrock, or anything beyond that small-verification scale needs the owner's explicit go-ahead first, the same way it already does for AWS infrastructure changes.

## Hand back to Claude

Stop editing the branch before Claude resumes. In `project/AGENT_HANDOFF.md`, set the active coordinator to Claude, record the branch/commit/worktree, tests actually run, unfinished tasks, and blockers. Claude then resumes under its existing guide. The user can request this switch directly.
