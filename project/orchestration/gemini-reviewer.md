# Gemini reviewer dispatch template

Combines spec-compliance and code-quality review in one pass (this worked well in practice — no need to split into two calls the way Claude subagent review does). Fill in the bracketed sections, run via `agy`. See `README.md` in this folder for the full command form and known constraints.

```
You are reviewing an implementation against its specification AND its
code quality, in one pass. Work from the current directory — read the
actual files and git history, don't just trust this brief.

## What was required

[Full task spec — same rigor as what was given to the implementer.
Paste exact required file content where it matters.]

## What actually happened (context, verify independently)

[What the implementer claims to have done, any judgment calls it made,
any known deviations (e.g. "the orchestrator had to fix X afterward
because of Y — was that the right call?"). Explicitly flag any
judgment call you want Gemini to independently assess rather than
rubber-stamp.]

## Your job

1. [Specific things to check — read the actual files, diff the actual
   commit, re-run tests/lint yourself rather than trusting the report.]
2. [Any specific judgment call you want assessed independently.]
3. Cross-check against any files this change depends on or integrates
   with.

Report back in this exact format:
- **Verdict:** APPROVED or CHANGES NEEDED
- **Spec compliance:** [assessment]
- **Code quality:** [assessment]
- **Issues (if any):** Critical / Important / Minor, with concrete
  evidence (file:line)
```

## Known constraints (as of 2026-09-29, see `project/BACKLOG.md` for the full pilot writeup)

- **Model:** `gemini-3.8-flash-medium` for routine review (genuinely thorough in practice — gave file:line-cited reasoning, independently re-ran tests/lint). Escalate to `gemini-3.1-pro-high` only if a review turns up something ambiguous enough to need deeper reasoning.
- **CLI is `agy`** (Antigravity), not the plain `gemini` CLI — the free-tier Gemini Code Assist client is deprecated and will hard-error ("migrate to Antigravity").
- **Flags required for headless review:** `--mode plan` (read-only — safe default for a reviewer that should never write) plus `--dangerously-skip-permissions` (needed even for read-only tools in headless mode, since there's no TTY to approve them interactively).
- **Command:**
  ```bash
  agy --model gemini-3.8-flash-medium --mode plan --dangerously-skip-permissions --print "$(cat <prompt-file>)"
  ```
- **Real limitation, not a Gemini-specific one:** review-by-reading does not catch integration bugs. A Codex implementation and two separate Gemini reviews all approved a Docker Compose setup that failed at runtime (MySQL 8 auth needs a Python package neither wrote into `requirements.txt`) — only caught by the orchestrator actually running `docker compose up` and hitting the endpoint. **Always follow an APPROVED review with real end-to-end verification before considering a task done.**
