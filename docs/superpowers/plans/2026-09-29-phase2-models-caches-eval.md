# Phase 2: Real Models, Caches, Limits, and Retrieval Evaluation

**Goal:** Finish DD1 Phase 2 locally: cited Bedrock answers, a repeated-question answer-cache hit, bounded model use, and a recorded retrieval baseline.

**Source design:** `docs/DESIGN.md` §§5.2, 6.7, 7.2–7.3, 8, 15, 17; `docs/DESIGN-004-action-plan.md` §5. DD2 and DD3 remain deferred as specified by DD4.

**Runtime:** Python service, Redis Stack, MySQL, Titan Text Embeddings V2 at 512 dimensions, Claude Haiku via Bedrock ConverseStream. Keep `GLASSBOX_PROVIDER=fake` as the local default; `bedrock` selects both real providers. Use AWS's default credential chain, never credentials in the repo. The `us-east-1` Titan model `amazon.titan-embed-text-v2:0` returned 512 dimensions on 2026-09-29; a Haiku call had already succeeded in the prior session. Set model IDs through environment variables and validate them at startup.

**Portability boundary:** API and ingestion depend on `EmbeddingProvider` and `LLMProvider`, never on boto3 or Bedrock response shapes. The provider factory is the only selection point; adding a non-AWS provider means implementing these contracts and choosing it there. The provider's `model_id` identifies the embedding vector space, so any switch requires a complete re-embedding before queries use the new model. Cache and limit logic should depend on focused interfaces with Redis implementations; local Docker and production can use the same Redis/MySQL protocols. AWS Terraform and deployment manifests are infrastructure-specific and require their own migration when changing cloud providers.

## Task 1 — Bedrock providers and model selection

- Add `boto3` as a runtime dependency and implement `services/glassbox/providers/bedrock.py` behind the existing `EmbeddingProvider` and `LLMProvider` interfaces. Titan requests contain `inputText`, `dimensions: 512`, and `normalize: true`; decode and validate each 512-float response. Empty batches return empty without an AWS call. Claude generation uses `converse_stream` with explicit `maxTokens <= 400`, a grounding system prompt, and emits text deltas as they arrive. Run blocking boto3 calls off the event loop and close streaming responses on cancellation.
- Add a provider factory/config module with explicit `fake` and `bedrock` modes. Use it in API and ingestion. Tests inject stub clients to cover payloads, output order, malformed responses, streamed deltas, failures, and mode selection. Keep existing fake-provider tests passing.
- Verify one low-token Haiku call and one Titan 512-dimensional call. Do not print prompts, credentials, or full embeddings in logs.

## Task 2 — Consistent embedding model switch

- Ingestion must re-embed a document when its stored `embedding_model` differs from the selected provider, even if `content_hash` is unchanged. Store the actual selected embedding model ID on each chunk. Skip only when both content hash and model match.
- A `bedrock` run re-embeds **both** corpora before asking questions with Titan. Do not search fake vectors with Titan query vectors. Keep the 512-dimensional Redis index; no schema migration is required.
- Tests cover unchanged-content/model skip, unchanged-content/model-change rewrite, and valid stored dimensions. Verify real re-ingestion against local MySQL/Redis and a representative query for each corpus.

## Task 3 — Three cache layers with versioning

- Add normalized exact-question embedding cache (7 days), corpus-versioned retrieval cache (1 hour), chunk-text cache (1 day), and corpus-versioned semantic answer cache (24 hours, cosine similarity >= 0.95) in Redis. The answer cache stores answer, citations, and trace needed to replay an honest hit; it is isolated by corpus and model ID.
- Bump `corpus:ver:{corpus}` only after a successful changed-document ingest. Cache keys include corpus version where content affects results; old keys expire naturally. Publish accurate cache stage events, preserve the shared per-request `seq` contract, and log `answer_hit` in MySQL.
- Tests exercise a second identical question without an LLM call, corpus isolation, model/version invalidation, retrieval and chunk hits, and concurrent requests. Verify an actual repeat through `/api/ask` against the local stack.

## Task 4 — Rate limit and daily model budget

- Implement an atomic Redis token bucket per salted client-IP hash: 10 questions per 10 minutes, 10-minute TTL, with `retry_after_s`. Honor only a trusted proxy address header in the deployed configuration; local direct requests use the socket IP.
- Implement an atomic UTC daily generated-answer counter, default cap 100, with two-day TTL. Reserve once before generating, but allow answer-cache hits without spending a slot. At the cap, still retrieve and return `done.mode = retrieval_only` plus source snippets; send no LLM request. Avoid logging raw IPs. Configurable test caps permit deterministic coverage.
- Tests cover boundary timing, concurrency, cache-hit exemption, rollover, and retrieval-only SSE. Verify the frontend displays the existing retrieval-only copy and rate-limit errors.

## Task 5 — Retrieval evaluation and acceptance

- Add `eval/questions.yaml` with about 30 public-safe questions across both corpora and expected source paths. Add `eval/run_eval.py` to compute recall@5 and MRR through the actual retrieval path, record a baseline by model/index/corpus version, and fail when recall@5 falls more than five percentage points below baseline. Keep fake-model scores separate from Titan scores.
- Run the eval on the Titan-indexed local corpus; inspect misses and revise content or ranking only with evidence. Run Python tests, Ruff, frontend lint/build, and a live API/worker/Redis/MySQL SSE request. Finish only when a cited answer is correct on representative questions and the repeated question shows an answer-cache hit.

## Handoff discipline

After each complete task, commit a working unit and update `project/SNAPSHOT.md` with what exists and `project/BACKLOG.md` with the next resume point, test evidence, and unresolved decisions. Update the design docs in the same change if implementation diverges from their contracts. No AWS infrastructure is provisioned in this phase.
