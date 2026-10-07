# Agent entry point

Agents other than Claude (e.g. Codex) are **reviewers** here, not implementers, and only when they have usage: the default review gate is an Opus subagent. When dispatched as a reviewer, follow the global `~/.claude/template/core/skills/review-gate/SKILL.md` and local `project/orchestration/reviewer-brief.md`: read `project/orchestration/reviewer-primer.md` first, review against the requirements, validate hands-on, and do not edit files, commit, push, merge, run `git stash`, read any Terraform state, tfvars or plan file, or touch live AWS/Kubernetes, and run pytest only with `GLASSBOX_TEST_MYSQL_PORT`/`GLASSBOX_TEST_REDIS_PORT` set to your own throwaway containers.

Before a full test suite, check that no other agent's full suite is running; serialize full suites and never dispatch an implementer whose suite would overlap yours. Sandboxed Codex can draft or review by reading; hands-on tests, local services or browser review require the owner's approval for that run's sandbox bypass (`project/orchestration/reviewer-brief.md`).

Read only as deep as the change needs: the relevant `docs/DESIGN*.md` section, then `project/SNAPSHOT.md` or `project/BACKLOG.md` ("> RESUME HERE") (older history in `project/archive/`). For frontend changes also read `project/MOBILE_DESIGN.md`.

If the owner explicitly hands implementation to another agent: read `project/BACKLOG.md` ("> RESUME HERE"), work in `.worktrees/<name>` on a branch, implement exactly the specified scope, record evidence in the PR's status report, and get a review before merge. The legacy Codex implementer guide is `project/archive/CODEX.md`.
