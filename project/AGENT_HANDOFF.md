# Agent handoff

**Project lead and next review owner: Claude. Codex implementation checkpoint complete through `fd0a60c`.** Claude's existing orchestration guide remains authoritative for Claude's workflow. Codex executed the approved local Phase 2 plan in reviewable commits for Claude to evaluate and correct.

## Taking over as the brain

1. Read this file, `SNAPSHOT.md`, `BACKLOG.md` (`> RESUME HERE` first), the active plan, and the relevant design sections. Claude follows its existing `CLAUDE.md` and `orchestration/README.md`; Codex follows root `AGENTS.md` and `CODEX.md`.
2. Inspect `git status --short`, current branch, recent commits, and worktrees. Treat uncommitted changes as another agent's work until you have understood them. Do not run two coordinators editing the same branch simultaneously.
3. Check the last recorded verification against the current code. Continue the next task from the backlog, including unfinished tests or external blockers. Do not silently promote a planned or partially implemented feature to complete.
4. At a meaningful checkpoint, update `SNAPSHOT.md` with verified repo state, move the `BACKLOG.md` resume pointer, and record commands/results and unresolved blockers here. Commit code and handoff docs together when the unit is complete.
5. When execution switches, update the lead/executor line and point the next agent to the exact commit, branch/worktree, and next action. The user may switch directly; no agent permission step is needed.

Keep each agent's working guide separate. `orchestration/` retains Claude's established delegation workflow. Codex's workflow is in `CODEX.md`. This file carries only the coordinator pointer and cross-agent checkpoint.

## Current checkpoint

- Branch: `main`; Codex's reviewable commits are provider `2e34103`, embedding cache `71ea3a5`, retrieval/chunk cache `4bf10e5`, answer cache `504dd7d`, limits `470f01c`, eval `febc800`, and local acceptance/Compose `fd0a60c`. A separate old worktree, `.worktrees/phase2-bedrock-providers`, is at `68a136e`; inspect before reusing it.
- Active plan: `docs/superpowers/plans/2026-09-29-phase2-models-caches-eval.md`.
- Phase 2 provider code is implemented: fake/Bedrock selection, Titan embeddings, Haiku streaming adapter, and model-aware re-embedding. Redis-backed embedding, retrieval, chunk, and semantic answer caches sit behind focused contracts. Atomic Redis rate and daily-budget adapters are wired through the API; `retrieval_only` returns source snippets and no LLM call. A 30-question retrieval harness has a fake-model smoke baseline. `GLASSBOX_PROVIDER=fake` remains the default. Compose forwards provider/model settings. Live fake-provider API/worker acceptance has run; live Haiku streaming and full Titan-indexed Q&A are not verified. Next: Claude reviews Codex commits and corrects mistakes; Titan baseline/cited answer after access and approval.
- Verification at this checkpoint: `.venv/bin/python -m pytest services/tests -q` → 96 passed; `.venv/bin/ruff check services eval`, `npm run lint`, and `npm run build` passed. Fake re-ingestion changed 9 docs on the final pass (one known secret-scanner fixture quarantined); `python -m eval.run_eval --write-baseline` and a normal run both reported recall@5 0.4667/MRR 0.3078 for 30 questions. A fresh host API/worker at port 8001 streamed ordered stage/retrieval/token/done for both corpora, with an answer-cache hit on the repeat; then the temporary API was stopped. The Docker API was rebuilt at port 8000, `/readyz` is healthy, and a live request streamed source snippets and done. A manually launched worker on the new code is running. Repository hooks pass. The fake LLM still produces its canned answer, so this is not cited Bedrock acceptance.
- A 512-dimensional Titan call succeeded in `us-east-1`. A live Haiku `converse_stream` call returned `ResourceNotFoundException` saying model use case details have not been submitted, although a prior Claude session recorded a successful nonstreaming `converse` call with the same inference profile. The code uses fake providers by default. Do not claim a live Bedrock answer until verified.
- `BACKLOG.md` asks before further paid Bedrock calls. Continue provider-independent work and seek owner input before another paid call. Do not submit model use-case details on the owner's behalf.
