# RAG quality and validation: implementation plan (planned, not built yet)

**Design:** `docs/DESIGN-005-rag-quality.md` (read sections 2, 5 and 6 first).
**Goal:** answers keep the specifics the sources contain, never invent, abstain correctly, and every retrieval or prompt change is measured before it ships.
**Ordering rule:** the harness (phases 1 to 4) comes before the changes it measures (phases 5 to 8). Each phase is a separate PR that can merge on its own. Phases marked **Autonomous: yes** need no owner action: no paid calls, no re-ingest of the live corpus, no infra. **Autonomous: no** names exactly what the owner must approve.

Standard checks for every phase: `ruff check services eval`, `pytest services/tests -q`, a status report in `project/status/`, SNAPSHOT/BACKLOG updates, and an Opus or Codex review per `project/orchestration/README.md`. No AWS, Bedrock or kubectl calls from agents unless the phase says the owner approved them.

| # | Phase | Autonomous? | Paid / re-ingest / infra |
|---|---|---|---|
| 1 | Golden dataset v2 and deterministic answer graders | **yes** | none |
| 2 | Retrieval eval v2 (k=8, chunk-level, noise@8) | **yes** | none |
| 3 | Paid baseline run of the current system (v13) | no | paid, about $0.03 |
| 4 | LLM judge plus owner calibration | partly (code yes, run no) | paid about $0.50 per run; owner labels about 50 answers per judge |
| 5 | Prompt v14: fix answer thinness | code yes, merge no | paid verification run; cache invalidation |
| 6 | Corpus hygiene, stale sweep, `--clear` | code yes, merge no | re-ingest on deploy; stale sweep deletes production rows/keys; corpus-scope decision; paid retrieval eval (about $0.0001) |
| 7 | Structure-aware chunks and breadcrumb headers | code yes, merge no | Alembic migration (`chunks.header`); full re-embed (under $0.01); paid eval |
| 8 | Hybrid BM25 plus vector with RRF | code yes, merge no | Redis schema change, re-ingest, paid eval |
| 9 | CI gate (free checks) | **yes** | none |
| 10 | Answer logging and online review | code yes, merge no | Alembic migration; logging decision |
| 11 | Paid-eval workflow in GitHub Actions (optional) | no | new OIDC role (Bootstrap workflow) |

---

## Phase 1 (planned): golden dataset v2 and deterministic answer graders

**Autonomous: yes.** Best first phase.

**Goal:** a versioned answer-level dataset and a runner that produces answers and grades them deterministically, usable with the fake provider (pipeline smoke) and, behind a flag, with Bedrock.

**Files**
- `eval/golden.yaml` (new): the ~70 cases from DESIGN-005 §5.1. Migrate the 30 `questions.yaml` cases verbatim (same ids) and add `gold_snippets` and `must_include` to them. Add the suggested questions from `frontend/src/suggested-questions.json`, the `planned`, `live`, `unanswerable`, `multi_turn` and `injection` categories. Mark about 20% of cases `holdout: true`. Fill `must_include` for About This System facts from the source files; mark About Basel facts `needs_owner_review: true`.
- `eval/schema.py` (new): load and validate (unique ids, known categories, regexes compile, every `gold_snippet` occurs in its expected source file in the repo, `history` alternates roles).
- `eval/graders.py` (new): pure functions `fact_coverage(answer, patterns)`, `abstained(answer)` (reuses `providers.base.is_exact_abstention` / `is_abstention`), `status_ok(answer, category)`, `rewrite_ok(rewrite, patterns)`, `injection_ok(answer)` (no system-prompt fragments).
- `eval/run_answers.py` (new): for each case, call the same building blocks the API uses: the rewrite prompt (`api/ask.py:_rewrite_prompt`), embedding, `search_chunks`, `_load_chunks`, `_prompt`, the provider's `generate`. Do this in-process, **not through `/api/ask`**, so the live rate limit, budget and answer cache are untouched. Write JSONL to `eval/runs/` (gitignored except committed baselines) with question, rewrite, retrieved chunk ids and paths, prompt version, answer, token usage, latency and grader results. Print a summary per category. `--paid` is required with `GLASSBOX_PROVIDER=bedrock` (mirror `GLASSBOX_EVAL_ALLOW_PAID`). Add `--cases` and `--category` filters and `--max-cases`.
- Keep `eval/questions.yaml` and `run_eval.py` working until phase 2 replaces them.

**Tests** (`services/tests/test_eval_graders.py`, `test_eval_golden.py`)
- Each grader on fixture answers, including the real thin stress-test answer, which must fail `fact_coverage`, and a v14-style answer, which must pass.
- Schema validation runs on the committed `golden.yaml` (this is what makes it a CI check).
- `run_answers` end to end with the fake provider against the CI MySQL/Redis services: produces one JSONL row per case and refuses Bedrock without `--paid`.

**Acceptance:** `pytest` green; `python -m eval.run_answers` with the fake provider runs every case locally; the summary lists per-category numbers; no network calls.

## Phase 2 (planned): retrieval eval v2

**Autonomous: yes.**

**Goal:** retrieval numbers that match production and catch noise.

**Files:** `eval/run_eval.py` (read `golden.yaml`; k=8 to match `worker/main.py:240`, keep @5 for continuity; chunk-level hit = a retrieved chunk's text contains a `gold_snippet`; `noise@8` = share of retrieved chunks under `services/tests/` or `docs/superpowers/plans/`; per-category output; baseline JSON gains these fields; the regression check gates chunk-level recall@8 and noise@8 as well as recall@5). Update `eval/README.md`. Fix DESIGN.md §15's claim that the eval runs in CI (it will, partly, after phase 9).

**Tests:** extend `services/tests/test_eval.py` for chunk-level scoring, noise@8 and the new regression rules.

**Acceptance:** fake-provider run works; old fields unchanged so the stored Titan baseline still parses.

## Phase 3 (planned): paid baseline of the current system

**Autonomous: no. Owner approves about $0.03 of Bedrock calls** (Titan question embeddings plus about 70 Nova Lite answers and 8 rewrites), run against a local stack ingested with Titan (re-ingesting locally costs under $0.01; it does not touch production).

**Goal:** the "before" numbers for v13: retrieval v2 and answer metrics.

**Files:** `eval/baselines/amazon.titan-embed-text-v2_0.json` (refresh, since the old fingerprint is stale), `eval/baselines/answers-nova-lite-v13.json` (new, summary plus per-case results), the status report.

**Acceptance:** the stress-test case fails `fact_coverage` (it reproduces the BACKLOG bug); the numbers are recorded in DESIGN-005 §2.2.

## Phase 4 (planned): LLM judge and calibration

**Autonomous:** the code is (it's tested with a fake judge); running it is not (paid about $0.50 per run; the owner chooses the model, DESIGN-005 §9 item 2). The owner also labels about 50 answers per judge, with failures oversampled (DESIGN-005 §5.3).

**Files:** `eval/judge.py` (two binary judges, faithfulness and relevance; JSON output `{pass, critique}`; model id from `GLASSBOX_JUDGE_MODEL_ID`; the judge sees only the question, the numbered chunks and the answer), `eval/calibration_candidates.py` (builds the label pool: v13 thin answers, abstention-disabled answers to unanswerable cases, and answers generated from perturbed sources, so each class has at least 15 cases), `eval/calibration.yaml` (owner labels: case id, answer hash, pass/fail, reason, `split: dev|test` assigned once by a fixed seed), `eval/calibrate.py` (per-class agreement on the **test split only**; prints dev-split disagreements for few-shot use and refuses to print test-split items' texts as few-shot material), wiring in `run_answers.py --judge`.

**Tests:** parsing and malformed-JSON handling with a fake judge; agreement math; the split is deterministic and dev/test never overlap; calibrate refuses to report when either class has fewer than 10 test cases.

**Acceptance:** true-positive and true-negative rates ≥ 0.85 on the held-out test split (at least 10 cases per class) before any gate uses the judge. Iterate on the prompt using dev-split disagreements only. If test-split agreement is used to choose between prompt versions more than once, draw fresh test labels.

## Phase 5 (planned): prompt v14, the answer-thinness fix

**Autonomous:** writing the change, unit tests and a fake-provider run are. Merging is not: it needs a paid before/after run (about $0.03) and it invalidates the live answer cache.

**Files:** `services/glassbox/api/ask.py` (`_prompt` wording per DESIGN-005 §6: remove "Use two or three concise sentences" and "Do not list every detail..."; add the keep-the-specifics instruction; `max_tokens` 400 to 500; `_PROMPT_VERSION = "v14"`), `services/glassbox/providers/bedrock.py` (raise the output-token guard at `:104-105` to 500; DESIGN-005 §2.1 "Generation" row), `services/tests/test_bedrock_providers.py` (the `:101` test asserts the new 500 limit and that 500 is accepted), `providers/base.py` `GROUNDING_RULES` if wording overlaps, `docs/DESIGN.md` §6.7 ("Max output 400 tokens" at `:241`), the deep dive's prompt description (it's ingested, keep it accurate).

**Tests:** prompt-contract unit test (the brevity phrases are gone, the specifics rule is present, the planned-marker text unchanged); a test that the `max_tokens` value `_stream` passes to the LLM is accepted by `BedrockLLMProvider.generate` (stub client), so the API limit and the provider guard can't drift apart again; existing `test_planned_labels.py` stays green.

**Acceptance (gate from DESIGN-005 §5.4):** the paid run goes through the real `BedrockLLMProvider` with `max_tokens=500` and no `ValueError`; `grep -rn 400` shows no leftover 400-token limit in `ask.py`, `bedrock.py` or DESIGN.md §6.7; `fact_coverage` rises (the stress-test case passes); unanswerable abstain = 100%; false-abstain ≤ 5%; planned/live = 100%; median answer length reported and reasonable (the owner judges); faithfulness ≥ 0.95 if phase 4 is calibrated. After deploy: re-ask the stress-test question live (one paid answer) and close the BACKLOG item.

## Phase 6 (planned): corpus hygiene, stale sweep and `--clear`

**Autonomous:** code yes; merge needs the owner's corpus-scope decision (DESIGN-005 §9 item 4) and a paid retrieval eval (about $0.0001). Merging triggers a re-ingest and corpus-version bump on the next deploy (no new embeddings needed for the deletions themselves). **On that deploy the stale sweep deletes production MySQL rows and Redis keys** for every document it no longer sees: list what it would delete in a dry-run log line first (`--dry-run-sweep` locally against a fake-ingested copy of the corpus) and include that list in the PR.

**Files:** `ingest/scanner.py` (exclude `services/tests/**` and `docs/superpowers/plans/**`, or tag them; optionally add `frontend/src/architecture.ts`), `ingest/run.py` (after a successful run, delete documents and chunks, MySQL rows and Redis keys, whose `source_path` was not seen in this scan; `--clear [--corpus about_me|about_system]` wipes MySQL chunks/documents and Redis `chunk:*` for the scope and bumps the corpus version), `ingest/redis_index.py` (a `kind` TAG field: doc/code/infra/manifest/test), `services/glassbox/retrieval/search.py` (accepts an optional kind filter), DESIGN.md §6.4, the deep dive, BACKLOG ("Ability to clear chunks" closed).

**Tests:** scanner exclusions; stale sweep removes a deleted file's rows and keys and leaves others; `--clear` per scope; `FT.ALTER` path for adding `kind` to an existing index. Restructure `ensure_index` (`ingest/redis_index.py:10-50`; its current behaviour is in DESIGN-005 §2.1 "Index migration") to compare against the full expected field list and add each missing field, so phase 8's `text` field also gets added.

**Acceptance:** fake-provider ingest has noise@8 = 0; a paid retrieval eval (about $0.0001) shows chunk-level recall@8 not lower than phase 3 and the rate-limit and budget cases now hitting `DESIGN.md`/`ask.py`.

## Phase 7 (planned): structure-aware chunks and breadcrumb headers

**Autonomous:** code yes; merge needs owner approval (an Alembic migration adding `chunks.header`, applied by the `migrate` Job; a full re-embed on the next deploy, under $0.01; caches invalidated) and a paid eval run.

**Files:** `ingest/chunkers/markdown.py` (one section per chunk at the deepest heading level that fits 120 to 450 words; merge only small sibling subsections; long sections split into windows that carry the breadcrumb; record the breadcrumb on `Chunk`), `chunkers/code.py` (merge tiny adjacent definitions to about 250 words; split over about 600; module-path prefix), `chunkers/base.py` (`Chunk.header`), `ingest/run.py` (embed `header + "\n\n" + text`; store the header in MySQL, the next Alembic revision adds `chunks.header`), `api/ask.py` `_prompt` (source label uses the header), `worker/main.py` `_load_chunks`.

**Tests:** migration up/down; chunker unit tests for the DESIGN.md §4.5–4.7 case (three chunks, not one), breadcrumbs on split windows, code merge/split bounds; prompt shows headers; planned-marker heading logic still applies.

**Acceptance:** chunk-level recall@8 and MRR improve on phase 6; answer metrics hold; chunk count and size distribution reported.

## Phase 8 (planned): hybrid BM25 plus vector with RRF

**Autonomous:** code yes; merge needs owner approval (Redis index schema gains a TEXT field; re-ingest to populate it) and a paid eval run.

**Files:** `ingest/redis_index.py` (`text` TEXT field holding header plus chunk text, added via `FT.ALTER` with a backfill from MySQL like the `model` backfill), `retrieval/search.py` (`hybrid_search`: KNN 20 plus `FT.SEARCH` BM25 20 with the same corpus/model filters. The lexical query is built from the question's terms: lowercase, drop English stopwords and one-character tokens, escape RediSearch punctuation (`:`, `-`, `@`, `.`, `{`, `}` and the rest) with backslashes, join with `|` (OR) inside `@text:(...)`. `FT.SEARCH` ANDs terms by default, so a whole question would match nothing and the phase would wrongly conclude that hybrid doesn't help. Use `SCORER BM25`; RRF k=60, optional kind prior, per-document cap 3, top 8), `worker/main.py` (passes the question text through; the retrieval cache key gains a `hybrid-v1` marker), `eval/run_eval.py` (reports the vector leg, the lexical leg and fused results separately), DESIGN.md §6.3, the deep dive. Check memory: Redis memory before and after on the local stack (expected +1 to 2 MB).

**Tests:** RRF math; lexical query builder (stopwords removed, terms OR-joined, a full natural-language question returns non-empty results on a fixture index, identifiers like `demo:load:lock` and `_PLANNED_SOURCE_SIGNAL` escaped and still matched); `ensure_index` adds `text` to an index that already has `model` and `kind`; per-document cap; empty lexical result; worker uses hybrid.

**Acceptance:** fused chunk-level recall@8 ≥ max(vector, lexical); identifier questions (add 3 to golden: `demo:load:lock`, `_PLANNED_SOURCE_SIGNAL`, `retrieval:jobs`) retrieved at rank ≤ 3; answer metrics hold. If fused results are not better than vector-only, don't merge; record the finding.

## Phase 9 (planned): CI gate (free checks)

**Autonomous: yes.** Can run in parallel with phases 3 to 8 once phases 1 and 2 have merged.

**Files:** `.github/workflows/ci.yml` (a step after pytest: fake-provider ingest of the real repo into the CI Redis/MySQL services, then `python -m eval.run_eval --lexical-only` against a committed lexical baseline; dataset schema validation; hygiene assertions), `eval/run_eval.py` (`--lexical-only` uses the BM25 leg from phase 8, or before phase 8 a plain `FT.SEARCH` over a temporary TEXT index built by the eval itself), `eval/baselines/lexical.json`.

**Tests:** the workflow step itself; a deliberate regression in a local branch fails it.

**Acceptance:** CI time increase under 60 seconds; no secrets or network calls needed.

## Phase 10 (planned): answer logging and online review

**Autonomous:** code yes; merge needs the owner's logging decision (DESIGN-005 §9 item 6).

**Files:** next Alembic revision (`queries.answer TEXT NULL`, `queries.abstained BOOL NULL`), `api/ask.py` `_save_query` (store the final answer, also on cache hits), `eval/sample_live.py` (pull N recent rows from a local MySQL dump or a read-only port-forward the owner runs, apply the deterministic graders and optionally the judge, emit candidate golden cases), the deep dive (ingested; say what is logged).

**Tests:** migration up/down; answer stored on full, cache-hit and stopped paths; abstained flag matches `done.abstained`.

**Acceptance:** a weekly review produces a short report; new real questions flow into `golden.yaml`.

## Phase 11 (planned, optional): paid eval in GitHub Actions

**Autonomous: no.** Infra change through the Bootstrap workflow, owner approval.

**Files:** `infra/bootstrap/main.tf` (role `glassbox-eval` with `bedrock:InvokeModel` on Titan V2, Nova Lite and the judge model only, OIDC subject from `local.github_oidc_subject_prefix`, environment `eval`), `.github/workflows/eval.yml` (`workflow_dispatch` plus an optional label trigger; brings up MySQL/Redis services, ingests with Titan, runs `run_eval` and `run_answers --paid --judge`, posts the summary as a PR comment, uploads JSONL as an artifact), `infra/CI.md`.

**Acceptance:** one approved run on a PR posts a comment; cost per run is shown in the comment from measured token usage.

---

## Deferred (planned later, only with eval evidence)

- Cohere Rerank 3.5 on Bedrock as a stage after RRF (only if the gold chunk sits at ranks 9 to 20 after phase 8).
- LLM-written contextual chunk prefixes (only if chunk-level recall@8 stays below 0.9 after phase 7).
- Redis 8.4 `FT.HYBRID` (when Redis is upgraded for another reason).
- Retrieval score threshold for abstention (only if phase 3/4 data shows low top scores predict unanswerable questions).
- Doc-level status metadata to replace `_PLANNED_SOURCE_SIGNAL` (separate design).
