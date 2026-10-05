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
- Hard rules: never read tfstate/tfvars/plan files; no terraform apply, no AWS/SSM/kubectl writes against live, no GitHub secret/environment changes; never `git stash`; pytest only against your own throwaway MySQL/Redis containers, with `GLASSBOX_TEST_MYSQL_PORT` and `GLASSBOX_TEST_REDIS_PORT` exported for every run (including `-x`, `-k` and single-test runs): without them the tests default to 3306/6379, the owner's shared local compose stack (`services/tests/stack_ports.py`), and leave test rows in it.
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
- Paid answer runs: the code default `BEDROCK_LLM_MODEL_ID` is Nova Lite (`us.amazon.nova-lite-v1:0`, the production model; Haiku is skipped), so no explicit setting is needed. A full Titan ingest takes about 45 minutes (sequential embedding): run it in the background.
- Prompt work on Nova Lite (phase 5): style rules work only after the question; it copies few-shot examples but often ignores rule lists; full runs vary by about ±3 passes, so compare several runs or the same-index v14. Smoke-test wording with `run_answers --cases` (under $0.002 each). Re-grade an old run with `grade_case` (pure) instead of re-running it. `git commit` needs `.venv/bin` on PATH; never `ruff format` a whole directory (it reformats `test_private_corpus.py`).
- The markdown chunker merges a short section into the next one while the pair stays under 500 words (the deep dive's cost section shares a chunk with "Observability: node diagnostics..."). Prompt v16 bisect: on Nova Lite a bare word-count rule or loosening the v15 tone line makes it copy whole sources (up to 460 words); test length rules on `me-current-role` and `system-budget` x3 before a full run.
- Prompt-only comparisons need no index: `python -m eval.run_answers --paid --replay eval/runs/<old>.jsonl` reuses that run's rewrites and retrieved chunks (no MySQL, Redis, embedding or ingest; about $0.03 per full run on Nova Lite). Since v17 the temperature is 0, but Bedrock is not fully deterministic (11 of 95 answers differed between two v17 runs), so still compare several runs. Never edit `ask.py` while a chained full run is going: each run is a new process and picks up the edit. In agent shells the auto-mode classifier may refuse to clone or list the private About Basel corpus; a replay of a stored run avoids needing it.
- Nova Lite copies a source sentence verbatim when it answers the question exactly (the cost section's "while the AWS EC2 T4g free trial lasts"): prompt rules, even in the system prompt, did not remove it (v17). Change the source wording instead.
- v17 round 1: on Nova Lite, an extra rule bullet after the question (first person, strict factuality) or extra few-shot lines can flip a false-premise system question to a bare abstention; ablate with a small script that drops one prompt part at a time (about $0.002 per variant) instead of rewording. A two-sided few-shot ("at <Company> ... and in my personal project ...") makes it invent the missing side.
- zsh does not word-split `$VAR`: `env $E cmd` with several assignments in E silently runs the fake provider. Export the variables instead.
- With `corpus/about-me-private` present, `load_golden` validates private gold snippets: a corpus resync can break `run_answers` until the snippets are updated.
- v17 round 2: a prompt rule that says "reply only <phrase>" for one question type gets applied to others (the absent-tech rule caught the false-premise db chip); teach narrow behaviors with one example instead. The resynced About Basel files are third-person prose: show the rewrite ("'Basel holds…' becomes 'I hold…'") in the system persona, or third person comes back.
- A full Titan ingest of both corpora into throwaway containers took about 30 minutes and about $0.02; ingest from a frozen copy (`git archive HEAD` plus the private checkout) so later edits don't change the index, and re-run it after a private pull: only changed files are re-embedded.
- Phase 8 (hybrid): RediSearch 7.2 does not split TEXT on newlines (`alpha\nbeta` indexes as one token), so `chunk_fields` stores `text` with whitespace collapsed; an escaped query term such as `k8s\/base` matches nothing, so `lexical_terms` splits on punctuation like the indexer. Bump `redis_index.TEXT_VERSION` when the indexed text changes shape (e.g. phase 7 breadcrumbs): the next reconcile rewrites every key (about 3 s for 670 keys).
- Tune retrieval for free: cache the golden questions' embeddings once (one Titan call each) and sweep `RetrievalConfig`s against the index; `run_eval` reports the vector and lexical legs (`legs`) next to the fused result. RRF ranks any chunk found by both legs above a strong vector-only hit: after changing hybrid settings re-check `planned-drive` (it said "Yes" until About This System kept the vector leg's top 4) and `sugg-system-db-terraform`.
- v18: the release image carried only `about-me/*.md` from the private checkout (`.dockerignore`), not the whole checkout; v18 adds an exception for `examples/approved-answers.yaml` (and its test). `services/tests/conftest.py` pins placeholder few-shots for every test, so a local private checkout never leaks into test results; tests that need approved examples point `GLASSBOX_APPROVED_EXAMPLES_PATH` at a synthetic fixture.
- The fresh index has 17 About Basel chunks, and all fun facts plus dev setup and recharging sit in one chunk; tone routing therefore uses the best-ranked chunk only (a rank-weighted top-3 score could not separate fun questions from team-culture/outage questions). `system-budget` flips between about 35 and 200 words under v17 and v18 alike on the same sources: compare it over several runs, not one.
- 14 of the 51 approved answers fail their own `golden` checks (stale from before the sign-off rounds); `eval/approved.py` marks them `known_failure`. Fix them in the private repo, not here.
- Measuring old vs new prompt on a fresh index: run the new prompt live, then the old prompt with `--replay` of each new run (retrieval doesn't depend on the prompt); build the old tree as `git archive` of the branch plus `git show origin/main:services/glassbox/api/ask.py`, with tiny stubs in its `run_answers.py` for new imports.
- The pre-commit ruff-format hook formats `test_private_corpus.py` differently from the venv's ruff: let the hook rewrite it, then `git add` again.
- Full runs back to back can hit Titan `ThrottlingException` on the embed call (7 errored rows in one 146-case run): re-run only the errored ids with `--cases` and merge the rows (the v18 status report scratch `merge.py` recomputes the summary with `summarize`).
- Phase 10: `_save_query` writes via `answer_log.submit` in a background task that `_stream` awaits only in `finally` (after `done`); tests that patch `ask._save_query` still see the call before the response ends. Drive `ask._stream(...)` directly to observe event order: httpx `ASGITransport` buffers the whole body.
