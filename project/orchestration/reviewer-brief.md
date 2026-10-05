# Reviewer brief (dispatch template)

The prompt every review-gate run gets, whoever the reviewer is: by default an **Opus subagent** (`Agent` tool, `model: "opus"`); **Codex** only when it has usage (command below). One pass covers spec compliance, code quality and hands-on validation. Fill in the bracketed sections, save the prompt to a uniquely named scratchpad file (`pr<n>-review-r<round>-prompt.md`), and dispatch. See `README.md` for the gate, the round cap and the merge sequence.

```
You are the review gate for a change in this repository. Review the
implementation against its requirements AND validate that it actually
works. Work from the current directory — read the actual files, the
diff, and the git history; don't trust this brief or the implementer's
report.

Read project/orchestration/reviewer-primer.md first: system map, hard
rules, known traps by area, per-area checklist. Never read or open any
terraform.tfstate file.

You may use the network, docker and local ports to validate, but NOT to
change anything:
- Do NOT edit, create, or delete tracked files. Do NOT commit, push,
  rebase, merge, or open/modify PRs.
- Do NOT touch live AWS or Kubernetes (no aws/ssm/kubectl against a
  real cluster, no terraform apply). NEVER read, open or copy any
  terraform.tfstate, *.tfvars or plan file.
- Never run `git stash`.
- Run pytest only against your own throwaway MySQL/Redis containers, with `GLASSBOX_TEST_MYSQL_PORT` and `GLASSBOX_TEST_REDIS_PORT` exported for every run (including `-x`, `-k` and single-test runs): without them the tests default to 3306/6379, the owner's shared local compose stack (`services/tests/stack_ports.py`), and leave test rows in it.
- You MAY run tests, linters, builds, `kubectl kustomize`, docker
  compose, local dev servers, and throwaway scripts outside the repo.
  Clean up anything you start. [If other agents share the machine:
  "Use ports X/Y; don't stop an already-running docker compose stack."]

## Requirements

[The spec — link the design doc sections (e.g. docs/DESIGN.md §9.3)
and restate the exact acceptance criteria. Same rigor as an
implementation spec.]

## Prior rounds

[Round 1: write "none". Later rounds: path to the previous result file
(e.g. <scratchpad>/pr<n>-review-r1-result.md), plus a list of
what was fixed since. Verify each earlier finding is actually fixed, then
look for regressions the fixes introduced.]

## PR status report

[Path to the PR's report in project/status/ (written when the PR opens),
or "none yet". Check that its claims match the code and that it carries
no account IDs, IPs or tokens.]

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

## Dispatching

- **Opus subagent (default):** `Agent` with `model: "opus"` and the filled prompt; run in the background and read its final report. Dispatch reviews for independent PRs in parallel (about 3 at once at most).
- **Codex (optional, only when it has usage):**

  ```bash
  codex exec -m gpt-6-sol --dangerously-bypass-approvals-and-sandbox \
    -C <worktree> -o <scratchpad>/pr<n>-review-r<round>-result.md \
    "$(cat <scratchpad>/pr<n>-review-r<round>-prompt.md)" < /dev/null
  ```

  Run it from Bash with `run_in_background: true` and read the `-o` file. **`< /dev/null` is required**: without it a backgrounded `codex exec` waits on stdin forever. A usage limit ends the run with an error in its log and no result file; switch to an Opus subagent instead of waiting. `-m gpt-6-sol` (`gpt-5.6-sol` stalls asking for approval headless; `gpt-6-luna` is weaker). Full permissions are deliberate (the `workspace-write` sandbox has no network, not even loopback); the prompt's rules are the guardrail.
- **Gemini (`agy`) is legacy**; see `project/archive/gemini-reviewer.md`. Never imply a reviewer approved when it didn't run.

## Known constraints

- **Review-by-reading misses integration bugs.** Always ask for at least one concrete end-to-end check in step 4, and still do your own verification before merge.
- **Not every finding needs a fix round.** Fix Critical/Important; a Minor finding about content that doesn't exist yet can be recorded as a known limitation in the code (docstring/comment) and BACKLOG, not just chat. After round 2, only blocking findings get another round (`README.md`).
- **Infra changes:** the hard rules in the prompt are not optional; keep them even for a docs-only infra change.
