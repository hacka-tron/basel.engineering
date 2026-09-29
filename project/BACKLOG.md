# BACKLOG

Bugs, stubs, future ideas, and the cross-session resume point. Update whenever a task completes or something new surfaces.

## > RESUME HERE

Phase 0 is done (merged to `main`, pushed to origin, commit `43f3807`). Next action: start DD1 Phase 1 (`docs/DESIGN.md` §17) — schema + migrations, ingestion for both corpora (files-in-repo approach, not yet DD3's pipeline), and the full SSE contract on the API/worker.

Not blocked on anything — M1 local work is independent of the M0 AWS-account items below.

## Multi-model pipeline notes (from the Phase 0 pilot, 2026-09-29)

Set up: Claude = architect/orchestrator (writes blueprints, does final judgment calls); Codex CLI = implementer; Gemini via `agy` (Antigravity) CLI = spec/quality reviewer. Findings worth remembering next time this is used:

- **Model access is plan-gated, not just a `-m` flag.** Under a ChatGPT (non-Pro) login, Codex only accepts `gpt-6-luna` — `sol`/`astra`/`terra`/bare `gpt-6` all reject with "not supported when using Codex with a ChatGPT account." After upgrading to ChatGPT Pro, `gpt-6-sol` worked and produced visibly cleaner, more focused output than `luna` (no wasted exploratory turns). Always verify which models are actually usable before relying on a model choice in a plan.
- **Codex's `workspace-write` sandbox cannot write git-worktree metadata.** A worktree's `.git` lives in the main repo's `.git/worktrees/<name>/`, outside the sandboxed workdir — so Codex can create/edit files but can't `git commit` inside a worktree. Working pattern: Codex implements, the orchestrator verifies and commits.
- **Codex needs `--dangerously-skip-permissions`-equivalent flags for read-only too, and `agy` needs `--mode plan --dangerously-skip-permissions` for headless review** — neither CLI's non-interactive mode has a native structured status protocol (DONE/BLOCKED/etc.) the way Claude subagents do; that has to be spelled out in every prompt.
- **Static review (even a good one) doesn't catch integration bugs.** Codex + two separate Gemini reviews all missed that MySQL 8's default auth (`caching_sha2_password`) needs the `cryptography` package for `aiomysql` — only found by actually running `docker compose up` and hitting `/readyz` for real. The lesson: the pipeline still needs an orchestrator-run, real end-to-end verification step; review agents (of any vendor) reading code is not a substitute for running it.
- **Gemini's reviews (via `agy`) were genuinely thorough** — caught nothing wrong, but gave real file:line-cited reasoning and independently re-ran tests/lint rather than trusting the brief.

## Open decisions (owner-only, can't be delegated to an agent)

- AWS account: Free vs. Paid plan — decide before Milestone 2 (AWS deploy). Free plan auto-closes the account after 6 months or when credits run out.
- Cloudflare origin protection: Worker-injected secret header vs. IP-range-only — IP-range-only is the current plan; revisit only if abuse becomes a concern.
- Frontend delivery mechanism on the EC2 node (baked into the API image vs. a separate static-file container) — decide at Phase 6.
- Project name: "Glassbox" is a placeholder (DD1 §19).

## Future milestones (not started)

- **M3 (DD2):** self-healing (ASG + Elastic IP reassociation), conversational chat memory + follow-up rewriting, live chat UX (Stop button, auto-scroll, localStorage persistence), Cloudflare-flavored streaming hardening.
- **M4 (DD3):** Google Drive + Git connectors, S3 raw zone, SQS + DLQ, KEDA ScaledJob ingestion, nightly reconciliation, blue-green re-embedding.

## Bugs / stubs

_(none yet — no application code has been implemented)_

## Ideas

_(none yet)_
