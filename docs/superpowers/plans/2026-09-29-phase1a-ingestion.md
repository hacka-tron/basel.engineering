# Phase 1a: Schema, Providers, Chunkers, Ingestion — Implementation Plan

> **For agentic workers:** This plan is executed via the multi-model pipeline in `project/orchestration/` (Codex implements, Gemini reviews, orchestrator verifies end-to-end) rather than Claude subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get real content (the `about_me` corpus, already written) chunked, embedded (via a fake provider), and queryable from MySQL + Redis's vector index — the data layer DD1 Phase 1 needs before the API/worker SSE contract (Phase 1b) can retrieve anything real.

**Architecture:** MySQL is the source of truth (`documents`, `chunks`, `ingestion_runs` tables per `docs/DESIGN.md` §7.1 — DD3's superseding schema is deferred to Milestone 4, not used here). Redis holds the vector index, rebuilt from MySQL. An `EmbeddingProvider`/`LLMProvider` interface (DD1 §6.2) is introduced now with only fake implementations — Bedrock comes in Phase 2. A standalone ingestion script walks the corpus, chunks by file type, embeds, and writes to both stores.

**Tech Stack:** Python 3.12, SQLAlchemy (models) + Alembic (migrations), `redis.asyncio` with the RediSearch/vector commands (`redis-stack-server`, already in `docker-compose.yml`), pytest.

## Global Constraints

- MySQL schema is DD1 §7.1 exactly (`docs/DESIGN.md` §7.1) — do not use DD3's `doc_uid`/`chunk_embeddings` schema (deferred to Milestone 4 per `docs/DESIGN-004-action-plan.md`).
- Embedding dimension: 512 (matches DD1 §6.7's Titan Text Embeddings V2 choice, even though the fake provider doesn't call Bedrock).
- No Bedrock/AWS calls anywhere in this phase — fake providers only.
- Follow the repo layout in `docs/DESIGN.md` §16: provider code under `services/glassbox/providers/`, ingestion under `services/glassbox/ingest/`, DB models/migrations under `services/glassbox/db/`.
- Use the dispatch templates in `project/orchestration/` (`gpt-6-sol` for implementation, `gemini-3.8-flash-medium` via `agy` for review). Orchestrator always runs real end-to-end verification (not just review approval) before marking a task done — see `project/BACKLOG.md` for why this matters.
- Commit after each task (per `project/CLAUDE.md`).

---

### Task 1: MySQL schema and Alembic migrations

**Files:**
- Create: `services/glassbox/db/__init__.py`
- Create: `services/glassbox/db/models.py` (SQLAlchemy declarative models)
- Create: `services/glassbox/db/session.py` (engine/session factory reading `MYSQL_*` env vars)
- Create: `alembic.ini`, `services/glassbox/db/migrations/env.py`, `services/glassbox/db/migrations/versions/0001_initial_schema.py`
- Test: `services/tests/test_db_models.py`

**Spec:** Reproduce this schema exactly (as SQLAlchemy models, then an Alembic migration that creates it):

```sql
CREATE TABLE documents (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  corpus        ENUM('about_me','about_system') NOT NULL,
  source_path   VARCHAR(512) NOT NULL,
  title         VARCHAR(512),
  content_hash  CHAR(64) NOT NULL,
  commit_sha    CHAR(40),
  updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_doc (corpus, source_path)
);

CREATE TABLE chunks (
  id              BIGINT PRIMARY KEY AUTO_INCREMENT,
  document_id     BIGINT NOT NULL,
  ordinal         INT NOT NULL,
  text            MEDIUMTEXT NOT NULL,
  start_line      INT,
  end_line        INT,
  token_count     INT,
  embedding       BLOB NOT NULL,          -- packed float32, 512 dims
  embedding_model VARCHAR(128) NOT NULL,
  FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
  UNIQUE KEY uq_chunk (document_id, ordinal)
);

CREATE TABLE ingestion_runs (
  id             BIGINT PRIMARY KEY AUTO_INCREMENT,
  commit_sha     CHAR(40),
  started_at     TIMESTAMP NOT NULL,
  finished_at    TIMESTAMP NULL,
  docs_changed   INT DEFAULT 0,
  chunks_written INT DEFAULT 0,
  status         ENUM('running','succeeded','failed') NOT NULL
);
```

(Omit the `queries` table from DD1 §7.1 — that belongs to the API/worker in Phase 1b, not ingestion.)

`session.py` should expose a `get_session()` (or equivalent) that reads `MYSQL_HOST`/`MYSQL_PORT`/`MYSQL_USER`/`MYSQL_PASSWORD`/`MYSQL_DATABASE` the same way `services/glassbox/api/db.py` already does (no defaults except port, matching the existing pattern and its documented rationale).

**Report format:** Status (DONE/BLOCKED), files changed, test results, any design decisions made.

- [ ] Dispatch to Codex per `project/orchestration/codex-implementer.md`, targeting the `phase1a-ingestion` worktree.
- [ ] Orchestrator: run `alembic upgrade head` against the real `docker-compose` MySQL container; confirm all 3 tables exist with `SHOW CREATE TABLE`.
- [ ] Dispatch to Gemini per `project/orchestration/gemini-reviewer.md`.
- [ ] Commit.

---

### Task 2: Provider interfaces and fake implementations

**Files:**
- Create: `services/glassbox/providers/__init__.py`
- Create: `services/glassbox/providers/base.py` (abstract `EmbeddingProvider`, `LLMProvider`)
- Create: `services/glassbox/providers/fake.py` (deterministic fake implementations)
- Test: `services/tests/test_providers.py`

**Spec:**

- `EmbeddingProvider.embed(texts: list[str]) -> list[list[float]]` (async) — returns one 512-dimension vector per input string.
- `LLMProvider.generate(prompt: str, *, max_tokens: int) -> AsyncIterator[str]` (async generator) — yields text chunks (simulating token streaming).
- `FakeEmbeddingProvider`: deterministic — the same input text must always produce the same vector (hash the text, e.g. via `hashlib.sha256`, and derive 512 floats from the hash bytes — no randomness, no external calls). Different inputs should produce different vectors; near-duplicate inputs do not need near-duplicate vectors (unlike a real embedding model, a hash-based fake has no semantic notion of similarity — that's fine and expected for this phase, since Phase 1a only needs *some* deterministic vector, not semantic quality).
- `FakeLLMProvider`: yields a short deterministic canned response, split into multiple chunks (to exercise streaming consumers later), for any prompt. Should not literally echo the whole prompt back (keep it simple: something like acknowledging it's a fake response, doesn't need to look like a real cited answer — the real prompt-following behavior is Phase 2's job with a real provider).

**Report format:** same as Task 1.

- [ ] Dispatch to Codex.
- [ ] Orchestrator: run tests directly (`pytest services/tests/test_providers.py -v`), confirm `FakeEmbeddingProvider` is genuinely deterministic across two separate calls with the same input (not just that the test happens to pass once).
- [ ] Dispatch to Gemini.
- [ ] Commit.

---

### Task 3: Markdown chunker

**Files:**
- Create: `services/glassbox/ingest/__init__.py`
- Create: `services/glassbox/ingest/chunkers/__init__.py`
- Create: `services/glassbox/ingest/chunkers/markdown.py`
- Test: `services/tests/test_chunker_markdown.py`

**Spec:** Same behavior already prototyped and verified working in an earlier throwaway comparison (see `project/BACKLOG.md` — the `gpt-6-sol` vs `gpt-5.6-sol` test used this exact spec and `gpt-6-sol` produced a correct, well-tested implementation). Reproduce that spec:

- Split by ATX headings (`^#{1,6} `). Content before the first heading is its own leading section.
- Target 300–500 tokens per chunk (approximate tokens as whitespace-separated word count — no tokenizer dependency), 50-token overlap between consecutive chunks *within* a split section.
- Sections under 300 tokens merge forward with the next section(s) until the combined size is at least 300 tokens, without exceeding 500 (start a new chunk instead of overshooting).
- Sections over 500 tokens split into multiple ~300–500 token windows with ~50-token overlap between consecutive windows.
- Every chunk records accurate 1-indexed, inclusive `start_line`/`end_line`, plus `source_path` (passed through) and `token_count` (actual word count of that chunk's text).
- Edge cases: empty input → `[]`; no headings at all → treat as one section; consecutive empty-ish headings merge forward; a single long line exceeding 500 tokens still splits on word boundaries.

Output type: a `Chunk` dataclass/model — align its fields with what `chunks` table (Task 1) and the embedding step (Task 2) will need: `text`, `source_path`, `start_line`, `end_line`, `token_count`.

**Report format:** same as Task 1, plus: "Any design decisions or ambiguities resolved on your own."

- [ ] Dispatch to Codex.
- [ ] Orchestrator: run tests directly.
- [ ] Dispatch to Gemini (ask it to specifically check the overlap and merge-forward logic against the spec's edge cases, not just that tests pass).
- [ ] Commit.

---

### Task 4: Terraform, YAML, and Python/TypeScript chunkers

**Files:**
- Create: `services/glassbox/ingest/chunkers/terraform.py`
- Create: `services/glassbox/ingest/chunkers/yaml_doc.py`
- Create: `services/glassbox/ingest/chunkers/code.py` (Python/TypeScript)
- Test: `services/tests/test_chunker_terraform.py`, `services/tests/test_chunker_yaml.py`, `services/tests/test_chunker_code.py`

**Spec (DD1 §6.4):**
- **Terraform:** one chunk per top-level block (`resource`, `module`, `variable`, `data`, `output`, etc. — anything at column 0 followed by `{`). Track `start_line`/`end_line` per block.
- **YAML:** one chunk per document (split on lines that are exactly `---`).
- **Python/TypeScript:** one chunk per top-level function or class definition (top-level = not nested inside another function/class). For Python, `def`/`class` at column 0. For TypeScript, `function`/`class`/`export function`/`export class`/`export const X = (...) =>` at column 0 — use reasonable judgment on what counts as a top-level declaration; this doesn't need a full parser, regex/line-scanning is fine given the repo's actual code style.
- All three: same `Chunk` shape as Task 3 (`text`, `source_path`, `start_line`, `end_line`, `token_count` — word-count approximation, same as the Markdown chunker, for consistency).
- No repo files of these types exist yet to test against for real (Terraform/YAML come in Phase 4) — write synthetic fixtures in the tests. The Python chunker *can* be tested against a real file in this repo (e.g. `services/glassbox/api/main.py`) as an additional integration-style test.

**Report format:** same as Task 1.

- [ ] Dispatch to Codex (can reasonably be one dispatch covering all three chunkers, since they're small and share the same output contract — use judgment; split into separate dispatches if the combined task feels like it's sprawling).
- [ ] Orchestrator: run tests directly.
- [ ] Dispatch to Gemini.
- [ ] Commit.

---

### Task 5: Ingestion script

**Files:**
- Create: `services/glassbox/ingest/run.py` (entry point: `python -m services.glassbox.ingest.run`)
- Create: `services/glassbox/ingest/scanner.py` (walks sources, computes content hashes)
- Create: `services/glassbox/ingest/redis_index.py` (writes vectors into Redis's vector index)
- Test: `services/tests/test_ingest_run.py` (can use `mysql`/`redis` from `docker-compose` directly rather than mocking — see verification note below)

**Spec (DD1 §6.4, §6.5, §7.2):**

- **Sources:** `about_me` = every `*.md` file under `corpus/about-me/` (already has real content — 5 files; *note 2026-10-02: these files moved to a private repo and are ingested as `private/...`, see DESIGN-003 §1.2*). `about_system` = files under an allowlist: `infra/`, `k8s/`, `services/`, `docs/` (adjust for this repo's actual layout — `frontend/src/architecture.ts` doesn't exist yet, skip it; most of `infra/`/`k8s/` don't exist yet either, so the allowlist should just skip nonexistent paths, not error). Denylist (always wins over allowlist): `*.tfvars`, `*.tfstate*`, `.env*`, `**/secrets/**`, and anything matching a simple secret-pattern check (reuse `detect-secrets`'s Python API if reasonably simple to wire up; otherwise a basic high-entropy-string heuristic is acceptable for this phase — note whichever you pick in the report). If the scanner finds a match, the ingestion run fails for that document (skip it, record the error, don't crash the whole run) rather than silently ingesting a potential secret.
- **Chunking:** pick the chunker by file extension (`.md` → markdown chunker, `.tf` → terraform, `.yml`/`.yaml` → yaml, `.py`/`.ts`/`.tsx` → code chunker). Unknown extensions: skip with a warning, don't crash.
- **Incremental:** compute `content_hash` (SHA-256 of file bytes) per source file; skip re-chunking/re-embedding a document whose hash matches what's already in `documents.content_hash`.
- **Write path:** for each changed document — upsert into `documents` (by `corpus`+`source_path`, matching the `uq_doc` constraint), delete its existing `chunks` rows, chunk the content, embed each chunk via `FakeEmbeddingProvider` (Task 2), insert new `chunks` rows (embeddings packed as `BLOB` via `struct.pack` or `numpy.tobytes()` — pick one, be consistent), and write the same vectors into Redis's vector index (`idx:chunks`, HNSW, cosine, 512 dims — create the index if it doesn't exist yet, tagged by `corpus` for filtering per DD1 §7.2).
- **Ingestion run tracking:** insert an `ingestion_runs` row at start (`status='running'`), update it to `succeeded`/`failed` with `docs_changed`/`chunks_written` counts at the end.
- **Idempotency:** running the script twice in a row with no source changes should produce zero new chunks/documents the second time (hash match short-circuits).

**Verification note:** this task is integration-heavy by nature (real MySQL + real Redis). Don't over-mock — the test file should spin up against the actual `docker-compose` MySQL/Redis (same pattern DD1 §15 describes for integration tests), not fake databases. The orchestrator will run the real thing end-to-end regardless of what the automated tests cover.

**Report format:** same as Task 1, plus explicitly list any judgment calls on the allowlist/secret-scanner approach.

- [ ] Dispatch to Codex.
- [ ] Orchestrator: with `docker compose up -d` running (MySQL + Redis only, API not required for this), run `python -m services.glassbox.ingest.run` for real. Verify: `documents` table has 5 rows (the real `about_me` files) with correct `corpus`/`source_path`/`content_hash`; `chunks` table has a sensible number of rows with real text from the actual bios; Redis has a populated `idx:chunks` index (`FT.INFO idx:chunks` or equivalent); re-running the script produces no new rows (idempotency check).
- [ ] Dispatch to Gemini for a spec-compliance + quality review of the diff.
- [ ] Commit.

---

## Self-Review Notes

- **Spec coverage:** Covers DD1 §17 Phase 1's "Schema + migrations. Fake providers. Ingestion for both corpora" — explicitly **not** covered here: "API + worker with the full SSE contract" (DD1's `curl -N` done-when criterion), which is Phase 1b, a separate plan once this data layer is verified working.
- **Placeholder scan:** No TBDs. Where exact code isn't prescribed (chunkers 2-4, ingestion script), behavioral specs are concrete and testable, matching how Task 3's spec (already proven with `gpt-6-sol`) reads.
- **Type/interface consistency:** `Chunk` shape is defined once in Task 3 and reused as the contract for Task 4's chunkers and Task 5's ingestion script. `EmbeddingProvider`/`LLMProvider` from Task 2 are consumed by Task 5.
- **Scope check:** Five tasks, roughly increasing in integration surface, ending in one real, verifiable outcome (real bio content, chunked and queryable in MySQL + Redis).
