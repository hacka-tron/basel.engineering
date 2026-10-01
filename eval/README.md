# Retrieval evaluation

`questions.yaml` contains 30 public-safe questions with expected source files. `run_eval.py` embeds each question with the selected provider and calls the same Redis `search_chunks` path as the retrieval worker at `top_k=5`. It reports recall@5 and MRR for each corpus and overall, plus the missed case IDs. Baselines are separate by embedding model and record the Redis index name, corpus versions, and a content fingerprint.

For the local fake-provider smoke baseline, start MySQL and Redis Stack, set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`, and `REDIS_URL`, then run:

```sh
GLASSBOX_PROVIDER=fake python -m services.glassbox.ingest.run
GLASSBOX_PROVIDER=fake python -m eval.run_eval
```

The committed `baselines/fake-v1.json` is a pipeline smoke baseline. Fake embeddings are deterministic but not semantic, so its score is **not** a quality target. A normal run fails if recall@5 falls more than five percentage points below the matching baseline. A changed corpus fingerprint requires inspecting misses and intentionally running `--write-baseline`.

For Titan, set `GLASSBOX_PROVIDER=bedrock`, re-ingest both corpora first, and obtain owner approval for paid calls before setting `GLASSBOX_EVAL_ALLOW_PAID=1`. Record the Titan baseline separately. Do not use fake-index scores as evidence of Bedrock retrieval quality.

## Golden answer set and deterministic answer checks

`golden.yaml` (version 2) is the answer-level dataset from DESIGN-005 §5.1: 75 public-safe cases. It contains the 30 `questions.yaml` cases copied verbatim (same ids, questions and expected sources), the 7 suggested-question chips, and new `planned`, `live`, `unanswerable`, `multi_turn` and `injection` cases. Each case can carry `expected_sources`, `gold_snippets` (text that must occur in one of those files), `must_include` / `must_not_include` (case-insensitive regexes), `rewrite_must_include` for follow-ups, `expect_abstain`, `holdout` (15 cases kept out of prompt tuning) and `needs_owner_review` (every About Basel case: they are claims about the owner, taken only from `corpus/about-me/`). `schema.py` validates the file, including that every gold snippet really occurs in its source file, and runs in CI through `services/tests/test_eval_golden.py`.

The 15 holdout cases were picked by hand when the set was created, spread across every category and both corpora (about 20% of each). The flags are frozen: don't move a case in or out of the holdout after seeing results, and don't tune prompts against holdout failures, or the holdout stops measuring overfitting.

Two more case fields: `live_but_off: true` (live cases only) accepts an answer that says the feature is installed but switched off, such as KEDA being suspended; `known_failure: <BACKLOG reference>` marks a case expected to fail until that item is fixed (today `me-site-stack`, the RDS error in `corpus/about-me/skills.md`). `run_answers` lists known failures separately under `known_failures` and keeps them out of every rate. A future CI or release gate must ignore `known_failure` cases (and should flag one that starts passing, so the field gets removed).

`graders.py` scores answer text with free, deterministic checks: `fact_coverage` (share of `must_include` matched), `abstained` (reuses the API's `is_abstention`), `status_ok` (planned answers must say no/not yet in the first sentence; live answers must not claim the feature is planned), `rewrite_ok` (the follow-up rewrite keeps the resolved entity) and `injection_ok` (no system-prompt fragments, no forbidden content). `grade_case(case, answer, rewrite)` combines them into `passed` plus a list of `failures`, so any stored answer can be re-scored without a model call. The stress-test case (`sugg-system-stress`) requires the 512 MiB capacity rule and the 5-minute cooldown, so today's thin v13 answer fails it.

`run_answers.py` produces answers in-process with the same building blocks as `/api/ask` (rewrite prompt, embedding, Redis KNN at k=8, MySQL chunk load, answer prompt, provider `generate`). It does not go through `/api/ask`, so the live rate limit, daily budget and answer cache are never touched. With MySQL/Redis running and the corpus ingested (see above):

```sh
GLASSBOX_PROVIDER=fake python -m eval.run_answers                       # all cases
GLASSBOX_PROVIDER=fake python -m eval.run_answers --category planned,live --max-cases 5
GLASSBOX_PROVIDER=fake python -m eval.run_answers --cases sugg-system-stress
```

It writes one JSON line per case (question, rewrite, retrieved chunk ids and paths, prompt version, answer, token counts, latency, grades) to `eval/runs/<UTC time>-<prompt version>-<git sha>.jsonl` (gitignored), a `.summary.json` next to it, and prints per-category numbers: pass rate, fact coverage, abstain rate on unanswerable cases, false-abstain rate, planned/live correctness, rewrite and injection checks, median answer length. With the fake provider this is a pipeline smoke test only; the fake model's canned answer fails almost every case by design. Any provider other than `fake` is refused unless both `--paid` is passed and `GLASSBOX_EVAL_ALLOW_PAID=1` is set (the same environment guard as `run_eval.py`), and a paid run needs the owner's approval first (plan phase 3):

```sh
GLASSBOX_PROVIDER=bedrock GLASSBOX_EVAL_ALLOW_PAID=1 python -m eval.run_answers --paid
```

The graders' known limitations (first-sentence planned/live check, verbatim-only leak detection) are listed in their docstrings in `graders.py`.
