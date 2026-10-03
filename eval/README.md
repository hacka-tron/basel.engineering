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

`golden.yaml` (version 2) is the answer-level dataset from DESIGN-005 §5.1: 90 public-safe cases. It contains the 30 `questions.yaml` cases copied verbatim (same ids, questions and expected sources), the 7 suggested-question chips, and new `planned`, `live`, `unanswerable`, `multi_turn` and `injection` cases. Each case can carry `expected_sources`, `gold_snippets` (text that must occur in one of those files), `must_include` / `must_not_include` (case-insensitive regexes), `rewrite_must_include` for follow-ups, `expect_abstain`, `holdout` (15 cases kept out of prompt tuning) and `needs_owner_review` (every About Basel case: they are claims about the owner, taken only from the private About Basel files, `private/...` in expected sources). `schema.py` validates the file, including that every gold snippet really occurs in its source file (for `private/...` sources only when a local checkout exists at `corpus/about-me-private/`; CI has none, so those snippets are unchecked there, and only the file name is checked against `KNOWN_PRIVATE_SOURCES` in `schema.py`), and runs in CI through `services/tests/test_eval_golden.py`.

The 15 holdout cases were picked by hand when the set was created, spread across every category and both corpora (about 20% of each). The flags are frozen: don't move a case in or out of the holdout after seeing results, and don't tune prompts against holdout failures, or the holdout stops measuring overfitting.

Two more case fields: `live_but_off: true` (live cases only) accepts an answer that says the feature is installed but switched off, such as KEDA being suspended; `known_failure: <BACKLOG reference>` marks a case expected to fail until that item is fixed (none today; `me-site-stack` carried it until the RDS error in the private `skills.md` was fixed). `run_answers` lists known failures separately under `known_failures` and keeps them out of every rate. A future CI or release gate must ignore `known_failure` cases (and should flag one that starts passing, so the field gets removed).

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

## LLM judges and calibration (RAG quality plan phase 4)

`judge.py` has two binary judges, **faithfulness** (is every claim supported by the numbered sources, judged by the sources alone) and **relevance** (does it answer the question asked). Each returns JSON `{"pass": bool, "critique": "..."}`; an unusable reply is recorded as an error, never as a pass. The judge sees only the question, the same numbered sources the generator saw (planned markers included) and the answer, never the golden `must_include` list. The model is `GLASSBOX_JUDGE_MODEL_ID`, default `us.amazon.nova-pro-v1:0` (Nova Pro through the `us.` inference profile, the same style the app uses for Nova Lite; the account needs Bedrock access to it). Changing the model or a rubric changes `JUDGE_PROMPT_VERSION` and invalidates a calibration.

**In a run:** `python -m eval.run_answers --judge` (with `--paid` and `GLASSBOX_EVAL_ALLOW_PAID=1` when the provider is not `fake`) adds a `judge` block to each row and `judge_faithfulness_rate`, `judge_relevance_rate` (answerable cases only), `judge_errors` and judge token counts to the summary. Rows now also store `sources` (the raw retrieved chunk text), so a stored run can be judged and labelled later. With the fake provider the judge is a stub that always passes (a pipeline smoke test). **Judge errors invalidate the rates:** if any judge reply in the run was unusable (`judge_errors` > 0), `judge_faithfulness_rate` and `judge_relevance_rate` are `null` (the partial figures are in `*_rate_partial`, for debugging only), because dropping errors would inflate them. A gate must treat a null rate as a failed run: fix or re-run until `judge_errors` is 0. For multi_turn cases the judge sees the standalone rewrite (stored as `judge.question`), not the bare follow-up. **Do not use a judge number as a gate until `calibrate` passes for it.**

### Building the label pool

Natural answers are almost all passes, so the pool oversamples failures. From a run made with this code (it needs the stored `sources`):

```sh
python -m eval.calibration_candidates --from-run eval/runs/<run>.jsonl            # free: thin + mismatched
GLASSBOX_PROVIDER=bedrock GLASSBOX_EVAL_ALLOW_PAID=1 \
  python -m eval.calibration_candidates --from-run eval/runs/<run>.jsonl --generate --paid
```

Kinds (aim for at least 15 of each): `thin` (answerable cases that miss facts, e.g. v13 answers), `abstention_disabled` (unanswerable questions answered with the refusal instruction removed), `perturbed` (answers generated from sources with a number changed or planned work stated as live, shown to you against the **original** sources, so they contain an unsupported claim), and `mismatched` (an answer written for another question paired with this one; answers come from answerable categories only, never abstentions; `calibrate` also reports agreement per kind (`test_by_kind`) so this easy kind cannot hide a weak one; this kind is an addition to the plan, because the other kinds rarely give the relevance judge a failing example). It writes three things: `eval/runs/calibration-pool.jsonl` (full items; gitignored because it contains private source text; the generated answers cannot be reproduced, so keep this file), `eval/runs/calibration-sheet.md` (what you read) and blank entries appended to `eval/calibration.yaml` (committed labels only; no questions, answers or sources). Re-running adds new items and never changes existing ones.

### Owner labelling workflow (about 50 answers, 1 to 1.5 hours)

0. The sheet marks every item `[DEV]` or `[TEST]`. Anyone iterating on the judge prompt (person or agent) must read only DEV items; reading TEST texts burns the held-out labels.
1. Open `eval/runs/calibration-sheet.md` (the question, the sources the answer should rely on, the answer) next to `eval/calibration.yaml`. Entries share an `id` such as `perturbed:sugg-system-stress`.
2. For each entry set `faithful` and `relevant` to `pass` or `fail` and give a one-line `reason`:
   - `faithful: pass` = every claim in the answer is supported by the sources shown. `fail` = a number, name or status the sources do not support, or a planned feature stated as working now. A refusal passes.
   - `relevant: pass` = it addresses the question asked, even briefly. `fail` = off topic, a different question, or a refusal.
3. Leave a label `null` to skip that judge for an entry. Do not edit `id`, `answer_hash` or `split`: the split (`dev` about 40%, `test` about 60%) comes from a fixed seed and the id alone, and `calibrate` rejects a changed split or a changed answer.
4. Label failures carefully; they are the point. Aim for at least 15 of each class per judge overall, which gives at least 10 per class in the test split.

### Calibrating

```sh
GLASSBOX_PROVIDER=bedrock GLASSBOX_EVAL_ALLOW_PAID=1 python -m eval.calibrate --paid            # dev split
GLASSBOX_PROVIDER=bedrock GLASSBOX_EVAL_ALLOW_PAID=1 python -m eval.calibrate --paid --split test
```

`--split dev` (the default) prints every dev disagreement with the judge's critique and your reason, to turn into few-shot examples and prompt fixes. `--split test` reports per-judge agreement on the held-out split only: true-positive rate (you pass, judge passes) and true-negative rate (you fail, judge fails). It never prints test texts, and it **refuses to report rates when either class has fewer than 10 test cases**. Acceptance is both rates at least 0.85 for a judge before any gate uses it. The tool logs each test run in `eval/runs/calibration-test-log.jsonl` and warns when the test split was already used with a different judge prompt or model: scoring prompt versions on the test split more than once overfits it, so draw fresh test labels (new candidates, new entries) instead.

Tests: `services/tests/test_eval_judge.py` (fake judge: parsing, malformed JSON, agreement math, the split, the refusal rule, the `--judge` wiring).
