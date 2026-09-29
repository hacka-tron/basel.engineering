# Agent handoff

**Active coordinator: Codex** (set by the user's 2026-09-29 request). This is a working pointer, not a permanent role assignment. The user may make Claude or another agent the coordinator at any time.

## Taking over as the brain

1. Read this file, `SNAPSHOT.md`, `BACKLOG.md` (`> RESUME HERE` first), the active plan, and the relevant design sections. Claude follows its existing `CLAUDE.md` and `orchestration/README.md`; Codex follows root `AGENTS.md` and `CODEX.md`.
2. Inspect `git status --short`, current branch, recent commits, and worktrees. Treat uncommitted changes as another agent's work until you have understood them. Do not run two coordinators editing the same branch simultaneously.
3. Check the last recorded verification against the current code. Continue the next task from the backlog, including unfinished tests or external blockers. Do not silently promote a planned or partially implemented feature to complete.
4. At a meaningful checkpoint, update `SNAPSHOT.md` with verified repo state, move the `BACKLOG.md` resume pointer, and record commands/results and unresolved blockers here. Commit code and handoff docs together when the unit is complete.
5. If handing back to Claude, change **Active coordinator** to Claude and point Claude to the exact commit, branch/worktree, and next action. If handing to Codex, change it to Codex with the same information. The user may switch directly; no agent permission step is needed.

Keep each agent's working guide separate. `orchestration/` retains Claude's established delegation workflow. Codex's workflow is in `CODEX.md`. This file carries only the coordinator pointer and cross-agent checkpoint.

## Current checkpoint

- Branch: `main`; provider checkpoint `2e34103`, followed by the embedding-cache checkpoint containing this update (inspect `git log -1 --oneline`). A separate old worktree, `.worktrees/phase2-bedrock-providers`, is at `68a136e`; inspect before reusing it.
- Active plan: `docs/superpowers/plans/2026-09-29-phase2-models-caches-eval.md`.
- Phase 2 provider code is implemented: fake/Bedrock selection, Titan embeddings, Haiku streaming adapter, and model-aware re-embedding. A Redis-backed embedding cache behind `EmbeddingCache` is also implemented. `GLASSBOX_PROVIDER=fake` remains the default. Live Haiku streaming and full Titan-indexed Q&A are not verified. Next code task: answer, retrieval, and chunk caches with corpus versioning; then limits and retrieval eval.
- Verification at this checkpoint: `.venv/bin/python -m pytest services/tests -q` → 82 passed; `.venv/bin/ruff check services` → all checks passed; `git diff --check` → clean. Tests include local MySQL/Redis integration cases. The existing API/worker processes may still be running older code; do not treat them as Phase 2 verification.
- A 512-dimensional Titan call succeeded in `us-east-1`. A live Haiku `converse_stream` call returned `ResourceNotFoundException` saying model use case details have not been submitted, although a prior Claude session recorded a successful nonstreaming `converse` call with the same inference profile. The code uses fake providers by default. Do not claim a live Bedrock answer until verified.
- `BACKLOG.md` asks before further paid Bedrock calls. Continue provider-independent work and seek owner input before another paid call. Do not submit model use-case details on the owner's behalf.
