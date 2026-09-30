# Multi-model orchestration

How work is split between models on this project, plus the dispatch template for the Codex review gate, so Claude (the orchestrator) fills in a template instead of re-deriving the boilerplate each time.

- `codex-reviewer.md` — dispatch template + CLI constraints for the Codex review gate. **Every change goes through it before being checked in** — "checked in" means merged to `main` (or a PR marked ready); work-in-progress commits on a feature branch come first and are what Codex reviews.
- `gemini-reviewer.md` — fallback reviewer (via `agy`) if Codex is unavailable.

## Roles (as of 2026-09-30 — owner moved Claude to a Max plan)

- **Claude Opus (orchestrator + implementer):** owns the blueprint, the plan, and the implementation. Opus decides per task which model does the hands-on work:
  - **Opus itself** — small or judgment-heavy changes where writing the spec would take longer than the change.
  - **Opus subagent** (`Agent` tool, `model: "opus"`) — larger features with real design/correctness risk (e.g. cache semantics, concurrency, security-relevant code).
  - **Sonnet subagent** (`model: "sonnet"`) — well-specified implementation, browser/dev-server verification loops (`claude-in-chrome` screenshots, DOM inspection), test/lint/build legwork.
  - **Haiku subagent** (`model: "haiku"`) — mechanical, low-risk edits and searches.
  - **Parallelize by default.** Independent work streams each get their own git worktree under `.worktrees/` and their own subagent, dispatched in the same turn. Give each concurrent stream distinct local ports (e.g. API 8000/8001, Vite 5173/5174) and tell them not to stop/recreate a shared `docker compose` stack.
- **Codex (`gpt-6-sol`) — reviewer and check-in gate, not implementer.** Before anything is merged to `main` or a PR is marked ready, Codex reviews the committed feature-branch diff the change against its requirements and **validates** it: reads the diff and the spec, re-runs tests/lint/build itself, and can bring up services to exercise the change. Runs in agentic mode with full permissions (`--dangerously-bypass-approvals-and-sandbox`) so it has network, can run `docker compose`, bind ports and write git metadata in worktrees. It still must not edit files, commit, push, or touch live AWS/Kubernetes — its output is a verdict, not changes. See `codex-reviewer.md`.
- **Fix loop:** Opus (or the implementing subagent) fixes every Critical/Important finding, then re-runs Codex on the updated diff. Two review rounds is the normal ceiling; if a third is needed, stop and bring the disagreement to the owner.

## What "checked in" means

1. Implementation + tests committed on a feature branch in its worktree (not yet checked in — this is the input to review).
2. Codex review returns **APPROVED** (or CHANGES NEEDED → fixed → APPROVED).
3. Real end-to-end verification done (review-by-reading doesn't catch integration bugs — see `gemini-reviewer.md`).
4. PR opened with the Codex verdict summarized in the description; merge after CI passes.
5. Anything that changes the live cluster on merge (Flux applies `k8s/overlays/prod` from the `deploy` branch) or grants new permissions (RBAC, IAM) needs the owner's explicit go-ahead before merge.

## Division of labor for git

Only Claude (orchestrator or its implementing subagents) commits and pushes. Codex has full permissions for validation but is instructed not to commit, push, or modify files.

## History

Until 2026-09-30 the roles were reversed: Codex (`workspace-write` sandbox, no network) implemented from exact specs, a Sonnet subagent did frontend verification, and Gemini reviewed. The pilot findings behind that setup are in `project/BACKLOG.md`; the Codex CLI constraints learned then (model choice, headless behavior) still apply and are carried into `codex-reviewer.md`.
