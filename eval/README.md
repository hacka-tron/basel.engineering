# Retrieval evaluation

`run_eval.py` embeds each question with the selected provider and calls the same Redis `search_chunks` path as the retrieval worker, at the worker's `top_k=8`. It answers "did retrieval put the right material in front of the model, and how much junk came with it?" Answer quality is a separate eval (`eval/golden.yaml` answer checks, RAG quality plan phase 1).

## Dataset

The runner reads `eval/golden.yaml` when it exists, otherwise `eval/questions.yaml` (30 public-safe questions with expected source files). `--questions PATH` picks a file explicitly. Either a top-level `questions:` (v1) or `cases:` (golden v2) list is accepted. Each case needs `id`, `corpus`, `question` and `expected_sources`; `category` (default `fact`) and `gold_snippets` are optional. A gold snippet is a short, verbatim piece of the source text that the right chunk must contain.

Only retrieval cases are scored here: cases without `expected_sources` (unanswerable, injection) and multi-turn cases with `history` are skipped. Production retrieves multi-turn cases with a paid LLM rewrite of the follow-up, so there is **no free retrieval signal for them**: embedding the raw follow-up would measure a query production never sends. What they retrieve only shows up in a paid run of the answer eval (`run_answers`), which uses the real rewrite and records the retrieved chunks. At least 25 retrieval cases are required.

## Metrics

All of them are reported overall, per corpus and per category.

| Field | Meaning |
|---|---|
| `recall_at_5`, `mrr` | File level: an expected source file is in the top 5; MRR over the top 5. Unchanged from v1, so old and new numbers compare. |
| `recall_at_8`, `mrr_at_8` | The same at the production k. |
| `chunk_recall_at_8`, `chunk_mrr_at_8`, `chunk_count` | Chunk level: a retrieved chunk comes from one of the case's `expected_sources` **and** its text contains one of its `gold_snippets` (case and whitespace runs ignored). The source check matters: tests quote doc prose verbatim, so a test chunk holding the snippet is noise, not a hit. Only cases that have snippets count (`chunk_count`); `null` when none do. This catches "right file, wrong section", which file-level scoring hides for large files like `docs/DESIGN.md`. |
| `noise_at_8` | Share of all retrieved chunks whose path starts with `services/tests/` or `docs/superpowers/plans/` (noisy chunks over retrieved chunks, not a mean of per-case shares). |

Each case also records `retrieved_sources` (top 5, as in v1), `retrieved_at_8` (chunk ids and paths), its file, chunk and noise results. `unreachable_gold_snippets` lists cases whose snippets no indexed chunk of an expected source contains (the snippet straddles a chunk boundary, or the text changed); those cases can never be chunk hits, so fix the snippet. The run prints misses at 5 and 8, chunk misses, noisy cases and unreachable snippets.

The run searches once at k=8 and takes the top 5 from that list, rather than running a separate KNN at k=5. HNSW is approximate, so this can in rare cases differ from the v1 runner's separate k=5 query.

## Baselines and the regression gate

Baselines are separate by embedding model (`baselines/<model>.json`) and record the Redis index name, corpus versions, a corpus content fingerprint and, from v2, `question_set_fingerprint` (ids, questions, expected sources and gold snippets). A normal run fails when:

- the embedding model, index or corpus fingerprint differs, or the question set differs (v2 baselines compare `question_set_fingerprint`; v1 baselines, which lack it, compare the sorted case ids): inspect the misses and refresh intentionally with `--write-baseline`;
- recall@5 drops more than 5 points;
- (v2 baselines) chunk-level recall@8 drops at all (no tolerance; DESIGN-005 §5.4 says it must not drop);
- (v2 baselines) noise@8 rises more than 5 points.

The committed `baselines/fake-v1.json` and `baselines/amazon.titan-embed-text-v2_0.json` are still in the v1 format. They parse unchanged and are gated on recall@5 and their case ids only. Their corpus fingerprints are stale, so the next run against either needs a reviewed `--write-baseline`, which writes the v2 fields. The Titan refresh is a paid run (RAG quality plan phase 3).

## Running it

For the local fake-provider smoke run, start MySQL and Redis Stack, set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` and `REDIS_URL`, then run:

```sh
alembic upgrade head
GLASSBOX_PROVIDER=fake python -m services.glassbox.ingest.run
GLASSBOX_PROVIDER=fake python -m eval.run_eval
```

Fake embeddings are deterministic but not semantic, so fake scores are a pipeline smoke check, **not** a quality target. `services/tests/test_eval.py` covers the metric math with synthetic retrieval results (no Redis), and `services/tests/test_eval_retrieval_e2e.py` runs a fake ingest plus `evaluate()` against the CI MySQL/Redis services.

For Titan, set `GLASSBOX_PROVIDER=bedrock`, re-ingest both corpora first, and obtain owner approval for paid calls before setting `GLASSBOX_EVAL_ALLOW_PAID=1`. Record the Titan baseline separately. Do not use fake-index scores as evidence of Bedrock retrieval quality.

## Golden answer set and deterministic answer checks

`golden.yaml` (version 2) is the answer-level dataset from DESIGN-005 §5.1: 75 public-safe cases. It contains the 30 `questions.yaml` cases copied verbatim (same ids, questions and expected sources), the 7 suggested-question chips, and new `planned`, `live`, `unanswerable`, `multi_turn` and `injection` cases. Each case can carry `expected_sources`, `gold_snippets` (text that must occur in one of those files), `must_include` / `must_not_include` (case-insensitive regexes), `rewrite_must_include` for follow-ups, `expect_abstain`, `holdout` (15 cases kept out of prompt tuning) and `needs_owner_review` (every About Basel case: they are claims about the owner, taken only from the private About Basel files, `private/...` in expected sources). `schema.py` validates the file, including that every gold snippet really occurs in its source file (for `private/...` sources only when a local checkout exists at `corpus/about-me-private/`; CI has none, so those snippets are unchecked there, and only the file name is checked against `KNOWN_PRIVATE_SOURCES` in `schema.py`), and runs in CI through `services/tests/test_eval_golden.py`.

The 15 holdout cases were picked by hand when the set was created, spread across every category and both corpora (about 20% of each). The flags are frozen: don't move a case in or out of the holdout after seeing results, and don't tune prompts against holdout failures, or the holdout stops measuring overfitting.

Two more case fields: `live_but_off: true` (live cases only) accepts an answer that says the feature is installed but switched off, such as KEDA being suspended; `known_failure: <BACKLOG reference>` marks a case expected to fail until that item is fixed (today `me-site-stack`, the RDS error in the private `skills.md`). `run_answers` lists known failures separately under `known_failures` and keeps them out of every rate. A future CI or release gate must ignore `known_failure` cases (and should flag one that starts passing, so the field gets removed).

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
