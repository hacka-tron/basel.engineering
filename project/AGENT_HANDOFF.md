# Agent handoff

**Project lead and review owner: Claude. Current implementation executor: Codex** (set by the user's latest request). Claude's existing orchestration guide remains authoritative for Claude's workflow. Codex is carrying out the approved Phase 2 plan and recording reviewable commits for Claude to evaluate.

## Taking over as the brain

1. Read this file, `SNAPSHOT.md`, `BACKLOG.md` (`> RESUME HERE` first), the active plan, and the relevant design sections. Claude follows its existing `CLAUDE.md` and `orchestration/README.md`; Codex follows root `AGENTS.md` and `CODEX.md`.
2. Inspect `git status --short`, current branch, recent commits, and worktrees. Treat uncommitted changes as another agent's work until you have understood them. Do not run two coordinators editing the same branch simultaneously.
3. Check the last recorded verification against the current code. Continue the next task from the backlog, including unfinished tests or external blockers. Do not silently promote a planned or partially implemented feature to complete.
4. At a meaningful checkpoint, update `SNAPSHOT.md` with verified repo state, move the `BACKLOG.md` resume pointer, and record commands/results and unresolved blockers here. Commit code and handoff docs together when the unit is complete.
5. When execution switches, update the lead/executor line and point the next agent to the exact commit, branch/worktree, and next action. The user may switch directly; no agent permission step is needed.

Keep each agent's working guide separate. `orchestration/` retains Claude's established delegation workflow. Codex's workflow is in `CODEX.md`. This file carries only the coordinator pointer and cross-agent checkpoint.

## Current checkpoint

- Branch: `main`; provider checkpoint `2e34103`, embedding-cache checkpoint `71ea3a5`, followed by the retrieval-cache checkpoint containing this update (inspect `git log -1 --oneline`). A separate old worktree, `.worktrees/phase2-bedrock-providers`, is at `68a136e`; inspect before reusing it.
- Active plan: `docs/superpowers/plans/2026-09-29-phase2-models-caches-eval.md`.
- Phase 2 provider code is implemented: fake/Bedrock selection, Titan embeddings, Haiku streaming adapter, and model-aware re-embedding. Redis-backed embedding, retrieval, and chunk caches sit behind focused contracts. Ingestion bumps corpus version on changed-document commits. `GLASSBOX_PROVIDER=fake` remains the default. Live Haiku streaming and full Titan-indexed Q&A are not verified. Next code task: semantic answer cache; then limits and retrieval eval.
- Verification at this checkpoint: `.venv/bin/python -m pytest services/tests -q` → 85 passed, including a repeated worker job showing only one vector search and MySQL load; Ruff and repository hooks run before commit. Tests include local MySQL/Redis integration cases. The existing API/worker processes may still be running older code; do not treat them as Phase 2 verification.
- A 512-dimensional Titan call succeeded in `us-east-1`. A live Haiku `converse_stream` call returned `ResourceNotFoundException` saying model use case details have not been submitted, although a prior Claude session recorded a successful nonstreaming `converse` call with the same inference profile. The code uses fake providers by default. Do not claim a live Bedrock answer until verified.
- `BACKLOG.md` asks before further paid Bedrock calls. Continue provider-independent work and seek owner input before another paid call. Do not submit model use-case details on the owner's behalf.
