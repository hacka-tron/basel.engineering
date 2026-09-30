# Codex reviewer dispatch template

Codex is the review-and-validation gate every change passes before check-in (see `README.md`). One pass covers spec compliance, code quality, and hands-on validation. Fill in the bracketed sections, save to a prompt file in the scratchpad, and run the command below.

```
You are the review gate for a change in this repository. Review the
implementation against its requirements AND validate that it actually
works. Work from the current directory — read the actual files, the
diff, and the git history; don't trust this brief or the implementer's
report.

You have full permissions (network, docker, ports). Use them to
validate, NOT to change anything:
- Do NOT edit, create, or delete tracked files. Do NOT commit, push,
  rebase, or open/modify PRs.
- Do NOT touch live AWS or Kubernetes (no aws/ssm/kubectl against a
  real cluster, no terraform apply).
- You MAY run tests, linters, builds, `kubectl kustomize`, docker
  compose, local dev servers, and throwaway scripts outside the repo.
  Clean up anything you start. [If other agents share the machine:
  "Use ports X/Y; don't stop an already-running docker compose stack."]

## Requirements

[The spec — link the design doc sections (e.g. docs/DESIGN.md §9.3)
and restate the exact acceptance criteria. Same rigor as an
implementation spec.]

## The change

Branch `[branch]` in `[worktree path]`, diff against `[base]`:
`git diff [base]...HEAD`.
[What the implementer claims it did, and every judgment call or known
deviation you want independently assessed rather than rubber-stamped.]

## Your job

1. Read the diff and every file it integrates with.
2. Check each requirement is met; cite file:line.
3. Re-run [exact test/lint/build commands] yourself and paste the
   summary lines.
4. [Specific end-to-end validation to perform, e.g. "bring up the local
   stack, ask a question and a follow-up, confirm X in the SSE stream".]
5. [Specific judgment calls to assess.]

Report in exactly this format:
- **Verdict:** APPROVED or CHANGES NEEDED
- **Requirements:** each criterion → met / not met, with evidence
- **Validation performed:** commands run + results
- **Issues:** Critical / Important / Minor, each with file:line and a
  concrete failure scenario
```

## Command

```bash
codex exec -m gpt-6-sol --dangerously-bypass-approvals-and-sandbox \
  -C <worktree> -o <scratchpad>/codex-review-<branch>.md \
  "$(cat <scratchpad>/codex-review-prompt.md)"
```

Run it from Bash with `run_in_background: true` (reviews of integration-heavy diffs take 5–10+ minutes), then read the `-o` file when it finishes.

## Known constraints

- **Model:** `-m gpt-6-sol`. `gpt-5.6-sol` stalls asking for approval in headless `exec` mode; `gpt-6-luna` is weaker — only fall back to it if `sol` is unavailable (test with a trivial prompt first).
- **Full permissions are deliberate** (owner decision, 2026-09-30): the old `workspace-write` sandbox had no network at all, not even loopback, so Codex couldn't validate anything that needed a DB, Redis, or a dev server. The prompt's "do not modify / do not touch live infra" rules are the guardrail — keep them in every dispatch.
- **Review-by-reading misses integration bugs.** Always ask for at least one concrete end-to-end check in step 4, and still do your own verification before merge.
- **Not every finding needs a fix round.** Fix Critical/Important; a Minor finding about content that doesn't exist yet can be recorded as a known limitation in the code (docstring/comment), not just chat.
