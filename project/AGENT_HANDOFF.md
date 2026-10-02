# Agent handoff

Short and current: who is coordinating, what state things are in, what is open, and where to look. Rewrite it (don't append) at each session end or coordinator switch. Older checkpoints: `project/archive/AGENT_HANDOFF-2026-09-29-to-10-01.md`.

## Current — Claude, 2026-10-02 (portfolio planning done; owner cleared the session)

**Active coordinator:** none running; the next session coordinates. **Review gate:** an Opus subagent with `project/orchestration/reviewer-brief.md`.

**Next action:** run the portfolio feature (BACKLOG "RESUME HERE" item 0). First merge the docs PR for branch `docs/portfolio-spec` (spec, three plans, this handoff; full CI because `docs/**` is corpus). Then dispatch PR 1 (`2026-10-02-open-to-work-footer.md`) and PR 2 (`2026-10-02-portfolio-corpus-backend.md`) in parallel worktrees; then PR 3a and, after PR 2 is deployed, PR 3b (`2026-10-02-portfolio-frontend.md`, split after its Task 3).

**Owner decisions in this round** (all in the spec §2): Portfolio is a third chat topic; desktop right pane follows the topic (no tabs); phones get Chat | Diagram | Portfolio; card grid; pull-up sheets at 80% for projects and for phone diagram components; 15–16px sheet text; footer "● Open to work" → "Open to full-time work and freelancing" + Copy email; "See portfolio" link only after the portfolio ships; Back from Diagram/Portfolio clears the selection.

**References (throwaway, never merge):** `mock/portfolio` (worktree `.claude/worktrees/agent-a01263ebcbb683826`) and `mock/hire-banner` (worktree `.claude/worktrees/agent-a8129ac02153a1001`). Remove both worktrees once PR 3b merges.

**Open PRs:** only the docs PR for `docs/portfolio-spec`, if opened.

**Known environment issue:** Docker Desktop on the owner's Mac is unresponsive, so local MySQL/Redis tests skip. Implementers must wait for CI's backend-tests before reporting.

**Where to look**
- Next actions: `project/BACKLOG.md` "> RESUME HERE"; owner-only decisions: its "Open decisions".
- Architecture and live state: `project/SNAPSHOT.md`. Per-change reports: `project/status/README.md`.
- Standing rules: `project/CLAUDE.md` "Working style". UI decisions: `project/MOBILE_DESIGN.md` "Owner decisions".

## Checkpoint format (when switching coordinator)

Replace "Current" above with: active coordinator; branch/worktree/commit; done; verification (exact commands and results); not verified; review (reviewer, round, verdict); open PRs (round, verdict, fixes since, merge order, waiting on whom); paid calls and live changes; next action; blockers. The incoming agent reads this file, `SNAPSHOT.md` and `BACKLOG.md`, checks `git status`, `git worktree list` and open PRs, and treats unknown uncommitted work as another agent's until understood. Never run two coordinators on one branch.
