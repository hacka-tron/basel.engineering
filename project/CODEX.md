# Codex guide

This guide applies when Codex is the active coordinator. Claude's original operating guide remains in `project/CLAUDE.md` and `project/orchestration/README.md`.

## Start a session

1. Read `project/AGENT_HANDOFF.md` for the current coordinator and exact checkpoint.
2. Read `project/SNAPSHOT.md` for architecture and repo state, `project/BACKLOG.md` for the next action, and the relevant design doc before implementation.
3. Inspect the current branch, `git status --short`, recent commits, and worktrees. Understand another agent's uncommitted work before changing it.

## Coordinate and implement

When Codex is the coordinator, Codex may write code, run services and tests, review the result, and commit working units directly. Use available tools and the current environment; do not assume the older Codex CLI sandbox or the Claude-led delegation pattern is in effect. Preserve the project's provider boundaries so local, AWS, and future cloud implementations can be swapped at the documented seams.

After a meaningful unit, update `project/SNAPSHOT.md` with verified state, `project/BACKLOG.md` with the next resume point, and `project/AGENT_HANDOFF.md` with exact test evidence and unresolved blockers. Commit a working, reviewable unit. If implementation changes the architecture, update the relevant design doc in the same change.

## Hand back to Claude

Stop editing the branch before Claude resumes. In `project/AGENT_HANDOFF.md`, set the active coordinator to Claude, record the branch/commit/worktree, tests actually run, unfinished tasks, and blockers. Claude then resumes under its existing guide. The user can request this switch directly.
