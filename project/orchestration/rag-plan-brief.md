# RAG plan brief (read this first for any RAG-plan task)

Shared context for every agent working on the RAG quality plan. It exists so agents don't re-derive it; read it instead of exploring. Plan: `docs/superpowers/plans/2026-10-01-rag-quality.md` (read only your phase). Design: `docs/DESIGN-005-rag-quality.md` (read only the sections your phase names). Owner-facing plan (validated 2026-10-03): https://claude.ai/code/artifact/1322d58c-7ca4-4e33-acbb-2355a8d137e3

## Owner decisions (2026-10-03)

Approved: the phase order below; paid runs (about $0.03 per baseline for phases 3, 5, 7, 8; agents may run them with the owner's local AWS credentials, profile `default`, user `basel-cli`); judge model **Nova Pro on Bedrock**; the owner labels about 50 answers for judge calibration; corpus scope **excludes `services/tests/` and `docs/superpowers/plans/`, frontend source not indexed**; turn the stale sweep to `apply` after one release log is checked; answer style "direct first sentence plus specifics, short list when needed, no bracketed citations"; answer logging (phase 10) yes. The earlier "no evaluations until more documents" hold is lifted.

Order: 0 golden-set refresh, 1 phase 3 paid baseline, 2 phase 6 part 2 (corpus scope), 3 phase 4 judge, 4 phase 5 prompt v15, 5 phase 7 chunks, 6 phase 8 hybrid, 7 phase 9 CI gate (free, any time), 8 phase 10 logging. Phase 11 optional, not started.

## Environment

- Docker Desktop works again (fixed 2026-10-03 by force-restarting it). The shared stack: `docker compose up -d` (mysql, redis, api). Never stop or recreate it; use your own ports if you start extra servers.
- Python: `.venv/bin/python`. Eval run steps: `eval/README.md` "Running it" (env vars, `alembic upgrade head`, ingest, `python -m eval.run_eval`; answers: `python -m eval.run_answers`).
- About Basel corpus is private: `git clone git@github.com:hacka-tron/basel.engineering-docs.git corpus/about-me-private` inside your worktree (git-ignored). Gold snippets for `private/...` sources are checked only against that checkout.
- Paid runs: `GLASSBOX_PROVIDER=bedrock`, `GLASSBOX_EVAL_ALLOW_PAID=1`. Report measured cost. Stop and report if a run would exceed $1.
- Docs under `docs/` are ingested into the live corpus. Keep `docs/architecture/deep-dive.md` sections under 500 words (one chunk each); the conversational chat section is at the limit.

## Rules for every task

- Worktree `.worktrees/<name>` on its own branch from `origin/main`; open a PR; **don't merge** (the orchestrator merges after review).
- Hard rules: never read tfstate/tfvars/plan files; no terraform apply, no AWS/SSM/kubectl writes against live, no GitHub secret/environment changes; never `git stash`.
- Checks: `ruff check services eval`, `pytest services/tests -q` (with the docker stack up, DB tests run too), a status report in `project/status/` per its README.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; PR bodies end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Final report: under 250 words. PR URL, what changed, checks and results, numbers, open questions.

## Token discipline

- Read this brief, then only the plan phase and DESIGN-005 sections you need. Use `grep -n` and line ranges, not whole files; `docs/DESIGN.md` (945 lines) and the deep dive (470+) are never read whole.
- Don't poll long jobs: run them in the background and wait for the completion signal.
- If you discover a fact the next agent would need (a command, a gotcha), add one line to "Gotchas" below in your PR.

## Gotchas

- zsh: `echo =====` fails (`=` expansion); macOS has no `timeout` (use `perl -e 'alarm N; exec @ARGV' cmd`).
- `git clone` over ssh fails in agent shells (host key verification): clone with https and `-c credential.helper='!gh auth git-credential'`.
- The shared compose MySQL can lag Alembic head (it was at 0003 on 2026-10-03, so portfolio/`ingestion_runs.notes` tests fail). For eval work start your own `mysql:8.0` and `redis/redis-stack-server` containers on spare ports and `alembic upgrade head` there; remove them when done.
- Pre-commit's ruff-format hook stashes unstaged files and fails: run `ruff format` on the files you changed (never repo-wide) before committing.
- Edit this brief through a process-doc PR (`project/**` only, no review round needed).
- The markdown chunker splits at every heading and embeds only chunk text (no file path), so splitting files doesn't change retrieval; heading wording does.
- Judge (phase 4, PR feature/rag-p4-judge): GLASSBOX_JUDGE_MODEL_ID defaults to us.amazon.nova-pro-v1:0. run_answers rows now store `sources`; calibration pool/sheet live in gitignored eval/runs/, labels in eval/calibration.yaml.
- Local migrations: `docker compose exec api alembic` fails (no alembic.ini in the image); run `MYSQL_HOST=127.0.0.1 MYSQL_USER=glassbox MYSQL_PASSWORD=glassbox MYSQL_DATABASE=glassbox .venv/bin/alembic upgrade head` from the repo root.
- Paid answer runs: set `BEDROCK_LLM_MODEL_ID=us.amazon.nova-lite-v1:0` (production model); the code default Haiku 4.5 is not enabled on the account. A full Titan ingest takes about 45 minutes (sequential embedding): run it in the background.
