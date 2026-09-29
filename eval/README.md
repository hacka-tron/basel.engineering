# Retrieval evaluation

`questions.yaml` contains 30 public-safe questions with expected source files. `run_eval.py` embeds each question with the selected provider and calls the same Redis `search_chunks` path as the retrieval worker at `top_k=5`. It reports recall@5 and MRR for each corpus and overall, plus the missed case IDs. Baselines are separate by embedding model and record the Redis index name, corpus versions, and a content fingerprint.

For the local fake-provider smoke baseline, start MySQL and Redis Stack, set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`, and `REDIS_URL`, then run:

```sh
GLASSBOX_PROVIDER=fake python -m services.glassbox.ingest.run
GLASSBOX_PROVIDER=fake python -m eval.run_eval
```

The committed `baselines/fake-v1.json` is a pipeline smoke baseline. Fake embeddings are deterministic but not semantic, so its score is **not** a quality target. A normal run fails if recall@5 falls more than five percentage points below the matching baseline. A changed corpus fingerprint requires inspecting misses and intentionally running `--write-baseline`.

For Titan, set `GLASSBOX_PROVIDER=bedrock`, re-ingest both corpora first, and obtain owner approval for paid calls before setting `GLASSBOX_EVAL_ALLOW_PAID=1`. Record the Titan baseline separately. Do not use fake-index scores as evidence of Bedrock retrieval quality.
