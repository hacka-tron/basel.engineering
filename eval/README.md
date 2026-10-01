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
