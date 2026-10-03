# Agent handoff

Short and current: who is coordinating, what state things are in, what is open, and where to look. Rewrite it (don't append) at each session end or coordinator switch. Older checkpoints: `project/archive/AGENT_HANDOFF-2026-09-29-to-10-01.md`.

## Current — Claude, 2026-10-02 (portfolio feature shipped; end of session)

**Active coordinator:** none. **Review gate:** an Opus subagent with `project/orchestration/reviewer-brief.md`. **Open PRs:** none (the wrap-up docs PR #151 is merged).

**Done this session:** the portfolio feature, all merged and deployed with Release and post-deploy Stream checks passing. #146 spec, plans and handoff; #147 footer "Open to work" (build-106; review r1 changes needed, fixed, r2 approved); #149 `portfolio` corpus and Alembic `0007` (build-107); #148 phone diagram details sheet (build-109; r2 found React Flow handles swallowing taps in the strip, fixed and orchestrator-reviewed at the round cap); #150 Portfolio topic (build-110, merge commit 7c889f2; r1 keyboard focus, fixed, r2 approved). Owner decisions: the Portfolio topic, segment and "See portfolio →" stay hidden until at least one published project exists; on phones any tap in the diagram strip closes the open sheet, on desktop clicking another card switches. The Dockerfile now copies `corpus/portfolio` into the frontend stage (build-110 is the first release built with it). Feature worktrees and branches are cleaned up; the `mock/portfolio` and `mock/hire-banner` worktrees are removed (local branches remain, never merge them). Reports: `project/status/2026-10-02-{open-to-work-footer,portfolio-backend,phone-details-sheet,portfolio-frontend}.md`.

**Next action:** the owner adds real projects under `corpus/portfolio/` (non-draft) with images in `frontend/public/portfolio/<slug>/`; the topic appears on the next release. **How to add a project:** follow `project/PORTFOLIO_PROJECT.md` (steps, first-project prerequisites, checks). Corpus changes run full CI; once merged, the release shows the Portfolio topic, phone segment and "See portfolio →". Before real projects land, the owner decides the planned-marker prompt change (BACKLOG, "Open items from the portfolio backend": portfolio sources must not get `[PLANNED, not built yet]`); golden cases for Portfolio only after real projects. Other BACKLOG items are unchanged.

**Environment (2026-10-03):** Docker Desktop works again (force-restarted). The shared compose MySQL may lag Alembic head; agents use their own containers for DB tests (see `orchestration/rag-plan-brief.md` Gotchas).

**Where to look**
- Next actions: `project/BACKLOG.md` "> RESUME HERE"; owner-only decisions: its "Open decisions".
- Architecture and live state: `project/SNAPSHOT.md`. Per-change reports: `project/status/README.md`.
- Standing rules: `project/CLAUDE.md` "Working style". UI decisions: `project/MOBILE_DESIGN.md` "Owner decisions".

## Checkpoint format (when switching coordinator)

Replace "Current" above with: active coordinator; branch/worktree/commit; done; verification (exact commands and results); not verified; review (reviewer, round, verdict); open PRs (round, verdict, fixes since, merge order, waiting on whom); paid calls and live changes; next action; blockers. The incoming agent reads this file, `SNAPSHOT.md` and `BACKLOG.md`, checks `git status`, `git worktree list` and open PRs, and treats unknown uncommitted work as another agent's until understood. Never run two coordinators on one branch.
