# Agent handoff

Short and current: who is coordinating, what state things are in, what is open, and where to look. Rewrite it (don't append) at each session end or coordinator switch. Older checkpoints: `project/archive/AGENT_HANDOFF-2026-09-29-to-10-01.md`.

## Current — Claude, 2026-10-02 (end of overnight session)

**Active coordinator:** none running. The last coordinator was Claude (Opus orchestrating worktree subagents). **Review gate:** an Opus subagent with `project/orchestration/reviewer-brief.md`. Process: `project/orchestration/README.md`.

**State:** the site is live and every merge to `main` deploys hands-off. 13 PRs merged overnight (#130–#135, #137–#143); summary in `project/status/2026-10-02-overnight-session.md`. zram is on. KEDA is suspended (owner hold). `warm-answers` is running. The alarms and snapshots Terraform apply is done.

**Open PRs**
- **#136 Ops · Reindex** (`feature/ops-reindex`, worktree `.worktrees/ops-reindex`): round 2 APPROVED, merged with main at 81fdd3b, CI green. **Waiting for the owner's go-ahead** (new privileged SSM document via the Terraform apply). Re-sync with main before merging.

**Waiting on the owner:** the #136 go-ahead and the apply approval; the SNS confirmation email; **Ops · List snapshots** / **Ops · Diagnose**. Other owner decisions are in BACKLOG "Open decisions".

**Known environment issue:** Docker Desktop on the owner's Mac is unresponsive, so local MySQL/Redis tests skip. Implementers must wait for CI's backend-tests before reporting.

**Where to look**
- Next actions: `project/BACKLOG.md` "> RESUME HERE"; owner-only decisions: its "Open decisions".
- Architecture and live state: `project/SNAPSHOT.md`. Per-change reports: `project/status/README.md`.
- Standing rules: `project/CLAUDE.md` "Working style". UI decisions: `project/MOBILE_DESIGN.md` "Owner decisions".

## Checkpoint format (when switching coordinator)

Replace "Current" above with: active coordinator; branch/worktree/commit; done; verification (exact commands and results); not verified; review (reviewer, round, verdict); open PRs (round, verdict, fixes since, merge order, waiting on whom); paid calls and live changes; next action; blockers. The incoming agent reads this file, `SNAPSHOT.md` and `BACKLOG.md`, checks `git status`, `git worktree list` and open PRs, and treats unknown uncommitted work as another agent's until understood. Never run two coordinators on one branch.
