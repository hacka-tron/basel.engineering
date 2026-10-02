# Agent handoff

Short and current: who is coordinating, what state things are in, what is open, and where to look. Rewrite it (don't append) at each session end or coordinator switch. Older checkpoints: `project/archive/AGENT_HANDOFF-2026-09-29-to-10-01.md`.

## Current — Claude, 2026-10-02

**Active coordinator:** Claude (Opus orchestrating parallel worktree subagents). **Review gate:** an Opus subagent with `project/orchestration/reviewer-brief.md`; Codex optional when it has usage; Gemini legacy. Process: `project/orchestration/README.md`.

**State:** the site is live and every merge to `main` deploys hands-off (release → ECR `build-N` → Flux). zram is on. KEDA is installed but **suspended** (scaled to 0) since the 2026-09-30 incident. `warm-answers` is running. Full live state: `project/SNAPSHOT.md` "Live state".

**Open PRs**
- None. [#126](https://github.com/hacka-tron/basel.engineering/pull/126) (About Basel from the private repo `hacka-tron/basel.engineering-docs`, with a personal-data guard) merged 2026-10-02; its release checked out the private repo successfully.

**Next action:** after that release deploys, ask the site an About Basel question and confirm the answer cites `private/...` sources. Then open a follow-up PR deleting the public `corpus/about-me/*.md` copies (BACKLOG item 6).

**Waiting on the owner (approval clicks, not commands)**
- **Alarms and snapshots (#124, merged):** run Bootstrap, approve the Terraform apply, confirm the SNS email. Until then the alarms, DLM snapshots and restore runbook aren't live (the uptime probe already runs). See `project/status/2026-10-01-alarms-snapshots.md`.
- **KEDA:** on hold by owner decision 2026-10-02 (memory too tight: ~274 MiB available). See BACKLOG.

**Parked by the owner**
- RAG quality plan is merged (`docs/DESIGN-005-rag-quality.md`, `docs/superpowers/plans/2026-10-01-rag-quality.md`), but **no evaluations (free or paid) until the owner adds more documents**.
- `corpus/about-me/` mirrors the owner's resume; don't edit it for architecture facts (e.g. the RDS line); raise it with the owner.

**Where to look**
- Next actions: `project/BACKLOG.md` "> RESUME HERE"; owner-only decisions: its "Open decisions".
- Architecture and live state: `project/SNAPSHOT.md`. Per-change reports: `project/status/README.md`.
- Standing rules: `project/CLAUDE.md` "Working style". UI decisions: `project/MOBILE_DESIGN.md` "Owner decisions".

## Checkpoint format (when switching coordinator)

Replace "Current" above with: active coordinator; branch/worktree/commit; done; verification (exact commands and results); not verified; review (reviewer, round, verdict); open PRs (round, verdict, fixes since, merge order, waiting on whom); paid calls and live changes; next action; blockers. The incoming agent reads this file, `SNAPSHOT.md` and `BACKLOG.md`, checks `git status`, `git worktree list` and open PRs, and treats unknown uncommitted work as another agent's until understood. Never run two coordinators on one branch.
