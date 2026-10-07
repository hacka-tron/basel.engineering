# Agent entry point

Agents other than Claude (e.g. Codex) are **reviewers** here, not implementers, and only when they have usage: the default review gate is an Opus subagent. When dispatched as a reviewer, follow the rules and report format in the prompt (`project/orchestration/reviewer-brief.md`): read `project/orchestration/reviewer-primer.md` first, review against the requirements, validate hands-on, and do not edit files, commit, push, merge, run `git stash`, read any Terraform state, tfvars or plan file, or touch live AWS/Kubernetes, and run pytest only with `GLASSBOX_TEST_MYSQL_PORT`/`GLASSBOX_TEST_REDIS_PORT` set to your own throwaway containers.

Read only as deep as the change needs: the relevant `docs/DESIGN*.md` section, then `project/SNAPSHOT.md` or `project/BACKLOG.md` ("> RESUME HERE") (older history in `project/archive/`). For frontend changes also read `project/MOBILE_DESIGN.md`.

If the owner explicitly hands implementation to another agent: read `project/BACKLOG.md` ("> RESUME HERE"), work in `.worktrees/<name>` on a branch, implement exactly the specified scope, record evidence in the PR's status report, and get a review before merge. The legacy Codex implementer guide is `project/archive/CODEX.md`.
