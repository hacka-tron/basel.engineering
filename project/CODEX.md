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

## Hand back to Claude

Stop editing the branch before Claude resumes. In `project/AGENT_HANDOFF.md`, set the active coordinator to Claude, record the branch/commit/worktree, tests actually run, unfinished tasks, and blockers. Claude then resumes under its existing guide. The user can request this switch directly.
