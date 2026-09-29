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

## Multi-model pipeline notes (from the Phase 1a pilot, 2026-09-29)

Same roles as Phase 0 (Claude orchestrates, Codex implements, Gemini reviews), applied to a much bigger, more integration-heavy phase (schema, providers, 4 chunkers, an ingestion job tying them together). More findings:

- **Codex's sandbox has zero network access, not even loopback.** It's not just "can't reach the package index" (Phase 0's finding) — it cannot reach `127.0.0.1:3306`/`127.0.0.1:6379` either, even with `docker compose` running on the host and the ports published. Any test requiring a real DB/service connection has to be skipped inside Codex's own dispatch and re-run independently by the orchestrator afterward. Codex handles this well when told about it upfront (self-reports `DONE_WITH_CONCERNS` and says exactly what it couldn't verify, rather than guessing) — always tell it explicitly in the dispatch that this limitation exists so it plans its own testing around it (unit-testable pieces now, integration deferred).
- **Behavioral specs work well for Codex on genuinely algorithmic tasks**, not just boilerplate. The Markdown chunker (merge-forward/overlap-window logic) and the ingestion script's denylist/secret-heuristic design were both given as prose specs, not literal code, and Codex made sound, well-reasoned implementation choices — a shift from Phase 0's "give exact code" style that saved real authoring time without costing quality, *as long as the review stage stays rigorous* (see below).
- **First-pass review still found two real Critical bugs on the chunkers task** (Terraform block detection truncating on inline nested braces; Python decorators getting detached from their functions) — both passed Codex's own tests, because Codex's synthetic fixtures didn't happen to exercise those cases. Gemini's review caught both, and a second review round after the fix caught that the fix itself had a residual gap (comment/string-literal contents can still throw off brace counting). **Two-stage review isn't paranoia — real, non-obvious bugs get through self-testing regularly, even from a strong implementer model.**
- **Not every review finding needs another round-trip.** After the second Terraform/decorator fix, the remaining findings (brace-counting inside string literals/comments, multi-line decorators, typed TS arrow functions) were accepted as documented known limitations instead of triggering a third fix cycle, because: no real Terraform or TypeScript files exist in this repo yet to be affected by them (those land in later milestones), and fully solving the string/comment-stripping issue needs real tokenization — disproportionate effort for code with zero real input right now. Judgment call: fix what's Critical or affects real content now; document (in the code, in a docstring, not just in chat) what's deferred and why, rather than chasing every edge case a reviewer can hypothesize.
- **Reusing already-verified throwaway code is a legitimate, fast path.** The Markdown chunker built during the earlier model-comparison pilot (see above) was copied directly into this repo rather than re-dispatched from scratch — saved a full Codex round-trip. Worth remembering: keep useful throwaway outputs from exploratory/comparison work instead of discarding them.
- **`detect-secrets`'s pre-commit hook will flag legitimate test fixtures repeatedly** (hardcoded dev passwords, fake API keys used to test a secret scanner). This isn't a one-time Phase-0 fluke — it happened again in Phase 1a (a test-fixture MySQL password). Expect `detect-secrets scan > .secrets.baseline` to be a routine step after any commit that adds new string literals resembling credentials, not an exceptional one.
- **`agy` reviews on larger/more file-heavy diffs can take longer than the default assumption** — a review covering 4 files (chunkers) or an integration-heavy task (ingestion script) sometimes needs 5-8 minutes, not the ~2-3 minutes smaller Phase 0 reviews took. Give `agy` calls a generous timeout (300-480s) rather than the tool default, especially for anything reviewing more than 1-2 files.
- **Real end-to-end verification keeps earning its keep.** Running the actual ingestion job against real corpus content (not just tests) surfaced a genuinely interesting correctness signal for free: the secret scanner correctly quarantined the ingestion pipeline's own test file, because that file legitimately contains a fake-AWS-key string as its own test fixture. Nothing broke — but this is exactly the kind of thing that only shows up by running the real thing against real data, not by reading code or running synthetic-fixture tests.

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
