# Basel reviewer brief inputs

Follow the global `~/.claude/template/core/skills/review-gate/SKILL.md` for the review procedure, verdict format and round handling. Read `project/orchestration/reviewer-primer.md` first for this site's system map, traps and per-area checks. Fill a uniquely named `pr<n>-review-r<round>-prompt.md` with the literal requirements, base/branch/worktree, diff, prior verdict and fixes, status report, exact validation commands, one realistic end-to-end check, and any known deviation. Record the reviewer and result in the PR body and status report.

## Copy into every relevant reviewer prompt

- Never read, open or copy any `terraform.tfstate`, `*.tfstate.backup`, `*.tfvars` or plan file.
- No `terraform apply`, no AWS/SSM/`kubectl` writes against the live system, no GitHub environment or secret changes. As a reviewer, do not touch live AWS or Kubernetes at all. Live changes happen only through the Terraform, Bootstrap and "Ops · ..." workflows after the owner's approval click; give the owner the click, never commands.
- Never run `git stash`. Do not edit, create, or delete tracked files; do not commit, push, rebase, merge, or open or modify PRs.
- Run pytest only against your own throwaway MySQL/Redis containers, with `GLASSBOX_TEST_MYSQL_PORT` and `GLASSBOX_TEST_REDIS_PORT` exported for every run (including `-x`, `-k` and single-test runs): without them the tests default to 3306/6379, the owner's shared local compose stack (`services/tests/stack_ports.py`), and leave test rows in it. Redis DB 15 is also used by endpoint tests.
- You MAY validate hands-on: run tests, linters, builds, `kubectl kustomize`, `docker compose`, local dev servers and throwaway scripts outside the repo, and use the network and local ports to do it, but not to change anything.
- Before a full suite, check that no agent's full suite is running. Serialize full suites. Use assigned API/Vite ports and do not stop or recreate the shared compose stack. Clean up only resources you started.

## Codex command choice

Codex is optional when it has usage; the Opus subagent is the default reviewer. For drafting or review that only reads code, use the sandbox:

```bash
codex exec -m gpt-6-sol -s workspace-write -C <worktree> \
  -o <scratchpad>/pr<n>-review-r<round>-result.md \
  "$(cat <scratchpad>/pr<n>-review-r<round>-prompt.md)" < /dev/null
```

For hands-on review that must run tests with loopback, local services, or a browser, the owner approves **that run** before replacing `-s workspace-write` with `--dangerously-bypass-approvals-and-sandbox`. The sandbox cannot reach loopback. Keep the same read-only reviewer restrictions in the prompt. A headless run needs `< /dev/null`; if Codex hits a usage limit, use the Opus reviewer.
