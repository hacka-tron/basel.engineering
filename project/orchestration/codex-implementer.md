# Codex implementer dispatch template

Fill in the bracketed sections and run via `codex exec`. See `README.md` in this folder for the full command form and known constraints.

```
[Exact task description — file paths to create/modify, exact required
code/config content where it matters, exact behavior. No placeholders:
if a file's content is prescribed, paste the literal content, not a
description of it.]

## Context

[One or two sentences: what this fits into, what came before, what
comes after. Name anything Codex must NOT touch (a Dockerfile it
shouldn't modify, a file another task owns, etc).]

## Your job

1. Implement exactly what's specified above.
2. [If tests are required: "Write tests covering X, Y, Z and run them
   yourself before reporting."]
3. Do NOT run `docker compose` / start long-lived services — that
   verification happens separately, outside your sandbox.
4. Do NOT commit — git commits in a worktree aren't writable from your
   sandbox (`.git/worktrees/<name>/` lives outside the workdir). The
   orchestrator verifies and commits separately.
   [Omit this line and give explicit `git add`/`git commit` instructions
   instead if NOT running in a worktree — Codex can commit fine in a
   normal checkout.]
5. Do not touch any file outside what's specified.

## Report format

- **Status:** DONE or BLOCKED (with a specific reason)
- **Diff / files changed:** [what, concretely]
- **Test results** (if applicable): paste actual output
- **Anything unexpected:** note it even if minor
```

## Known constraints (as of 2026-09-29, see `project/BACKLOG.md` for the full pilot writeup)

- **Model:** use `-m gpt-6-sol`. Verified head-to-head against `gpt-5.6-sol` on a real task (Markdown chunker, DD1 §6.4) — `gpt-6-sol` delivered complete, correct, tested code; `gpt-5.6-sol` produced *zero* files because it stalled asking for design approval, which non-interactive `exec` mode has no way to answer. Do not use `gpt-5.6-sol` for headless dispatch. `gpt-6-luna` (the silent default) is usable but noticeably weaker/more meandering — only fall back to it if `sol` access is unavailable (plan-gated, not just a flag — test with a trivial prompt first if unsure).
- **Sandbox flag required:** `-s workspace-write` (default is `read-only`, which can't write files at all).
- **Working directory:** `-C <path>` to target a specific directory; add `--skip-git-repo-check` if the target isn't a git repo.
- **Git worktrees:** Codex's sandbox cannot write `.git/worktrees/<name>/` (outside the worktree dir). Have Codex create/edit files only; the orchestrator runs `git add`/`git commit` afterward.
- **No network access at all, not even loopback.** Codex can't reach `127.0.0.1:<port>` even if `docker compose` is running on the host with ports published — this isn't just "can't pip install," it's zero network. Tell Codex explicitly in the dispatch when a task needs a real DB/service connection to test: ask it to implement everything, run what's unit-testable without a live service, and report `DONE_WITH_CONCERNS` for the parts it can't verify rather than guessing. The orchestrator then runs those integration tests independently against the real `docker compose` stack.
- **Behavioral specs (prose + edge cases + expected report) work well for algorithmic logic**, not just literal code-to-reproduce. Don't feel obligated to hand-write every implementation detail — a clear spec with edge cases gets good results and saves authoring time. This makes the two-stage review *more* important, not less: give Codex latitude on *how*, but always verify *what* it built, independently, every time (see `gemini-reviewer.md`) — self-tests passing does not mean the logic is right, even from a strong implementer.
- **Command:**
  ```bash
  codex exec -m gpt-6-sol -s workspace-write -C <dir> "$(cat <prompt-file>)"
  ```
  (Add `--skip-git-repo-check` if `<dir>` isn't a git repo/worktree.)
