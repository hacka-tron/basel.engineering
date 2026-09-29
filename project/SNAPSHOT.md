# SNAPSHOT

Architecture and repo-state blueprint. Read this first when starting a new session — it should make scanning the repo unnecessary for orientation.

**Last updated:** 2026-09-29

## What exists right now

- **Design docs** (`docs/DESIGN.md`, `docs/DESIGN-002-followups.md`, `docs/DESIGN-003-ingestion.md`, `docs/DESIGN-004-action-plan.md`): fully specify the target system ("Glassbox" — a RAG portfolio chatbot with a live architecture diagram) and the build sequencing. Cloudflare (not CloudFront/S3) is the edge/TLS layer throughout — see DD1 §10.3.
- **DD1 Phase 1 is fully done and merged to `main`** (pushed to origin) — the entire local backend, verified end to end with a real `curl -N` against a live worker + API. What exists:
  - **Data layer (Phase 1a):** MySQL schema + Alembic migrations (`services/glassbox/db/`: `documents`/`chunks`/`ingestion_runs`/`queries`, DD1 §7.1), fake `EmbeddingProvider`/`LLMProvider` (`services/glassbox/providers/`), four chunkers — Markdown/Terraform/YAML/code (`services/glassbox/ingest/chunkers/`) — and an ingestion script (`services/glassbox/ingest/{scanner,redis_index,run}.py`).
  - **Serving layer (Phase 1b):** a Redis Streams job queue + retrieval worker (`services/glassbox/worker/main.py`, `services/glassbox/retrieval/search.py`) doing real KNN vector search against `idx:chunks`, and `POST /api/ask` (`services/glassbox/api/ask.py`) streaming the full SSE trace contract (`stage`/`retrieval`/`token`/`done`/`error`, DD1 §8) — both processes share one Redis-backed `seq`/`t_ms` sequencing convention (`services/glassbox/trace.py`).
  - Real content: 5 `about_me` docs + 38 `about_system` docs → 174+ chunks. A live `curl -N` against both corpora produces correct, fully-ordered SSE streams with real retrieved chunks and a `queries` row logged per request.
  - **Locally runnable end to end:** `docker-compose.yml` brings up MySQL + `redis-stack-server` + `api`; the retrieval worker runs as a separate manual process. Verified interactively via real `curl -N` SSE requests against both corpora.
- **DD1 Phase 3 (frontend) is fully done and merged to `main`** — `frontend/` (Vite + React 19 + TypeScript + Tailwind v4 + `@xyflow/react`), now wired live to the real local backend, not just a static mock:
  - Desktop layout: header (site name, `mailto:` "Contact me" link, corpus toggle, GitHub link), chat panel with suggested-question chips and a real message list (`frontend/src/components/Chat.tsx`), and a live architecture diagram of all 10 pipeline nodes (`frontend/src/components/ArchitecturePanel.tsx`, `frontend/src/architecture.ts`, matching DD1 §8's `NodeId` union and §5.1 flow topology), plus `StatsBar` showing real per-request numbers.
  - Mobile layout (below Tailwind's `md`/768px breakpoint): chat takes full width, the diagram collapses to a horizontal "pipeline strip" (`frontend/src/components/PipelineStrip.tsx`) above the chat input, and a "View architecture" button opens the full diagram as an accessible bottom sheet (Escape-to-close, focus managed). Only one `ArchitecturePanel`/React Flow instance is ever mounted at a time (`frontend/src/hooks/useMediaQuery.ts`-driven conditional mount, not CSS-only hiding).
  - **Live wiring (`frontend/src/lib/sse.ts`):** a hand-rolled `fetch()` + `ReadableStream` SSE client (native `EventSource` doesn't support POST, and `/api/ask` is POST) parses the `stage`/`retrieval`/`token`/`done`/`error` contract and drives real state in `App.tsx`: the diagram highlights the currently-active pipeline node in `cyan` (the one reserved accent color) as stage events arrive, "Retrieved chunks" shows real results with citation links when present, chat messages stream in token by token, and `StatsBar` shows honestly-labeled last-request numbers (not a fabricated rolling p50/hit-rate, since no such window is computed yet). A ref-based in-flight guard prevents double-submission races that `isStreaming` state alone wouldn't fully close. Backend is untouched — still the fake LLM/embedding providers, so answers are always the same canned sentence, but retrieval, the SSE contract, and the diagram are all real.
  - Design tokens in `frontend/src/index.css` (`@theme`): dark theme, one accent color (`cyan`, reserved for "active" states), JetBrains Mono throughout. Two Tailwind v4 gotchas documented there in comments: `@theme inline` var-to-var aliasing resolves empty (hardcode hex values instead), and custom color tokens must avoid Tailwind's reserved scale keys (`base` collided with `--text-base`, renamed to `canvas`).
  - Verified in a real browser against the real running backend (desktop, mobile, both corpora, a synchronous double-submit race) via `claude-in-chrome` — no console errors, no bugs found on the wiring pass.
- **Real `about_me` corpus content exists** (`corpus/about-me/*.md`: `bio.md`, `microsoft.md`, `google.md`, `projects.md`, `skills.md`), sourced from Basel's actual resumes. No `bullet-bank.md` (no source doc to export from) and no Glassbox entry in `projects.md` yet (not deployed).
- **AWS CLI configured locally; no production AWS resources provisioned.** A 512-dimensional Titan Text Embeddings V2 call succeeded in `us-east-1`. A previous Claude session recorded a successful nonstreaming Haiku call, but this session's Haiku `converse_stream` attempt returned a model use-case-details error; streaming access remains unverified. A `$20/month` budget named `Glassbox-Monthly` exists. Temporary EC2, RDS, and Lambda resources used for AWS credit activities were deleted; a read-only check found no active EC2 instances, RDS instances, or `glassbox-hello` Lambda function in `us-east-1`. No Terraform written.
- **DD1 Phase 2 provider boundary implemented in source:** `GLASSBOX_PROVIDER=fake|bedrock` selects embedding and LLM adapters from one factory. Fake remains the default. The Bedrock adapter uses Titan V2 for 512-dimensional vectors and Haiku ConverseStream for text. Ingestion now re-embeds unchanged documents when the stored embedding model differs, and stores the selected model ID. API and ingestion use the provider interfaces rather than direct Bedrock calls. Unit and local MySQL/Redis integration tests pass. A live end-to-end cited Bedrock answer has **not** been verified; caches, limits, and retrieval evaluation remain pending.
- **Agent handoff:** Claude's original `project/CLAUDE.md` and `project/orchestration/README.md` remain intact. Codex has a separate `project/CODEX.md` and root `AGENTS.md`. `project/AGENT_HANDOFF.md` records the active coordinator (currently Codex) and the exact resume point.
- **Domain:** `basel.engineering`, DNS on Cloudflare (proxied). Nothing deployed there yet.
- **Skills installed in this repo** (`.agents/skills/`): the `claude-mem`, `impeccable`, `task-observer`, and `omniroute` suites, plus the built-in `superpowers` set used to write the design docs and plans.
- **Multi-model orchestration:** Claude's established leader/implementer/reviewer process is preserved in `project/orchestration/README.md`. Codex's separate guide describes the current Codex-led workflow. Current coordinator and switch procedure are in `project/AGENT_HANDOFF.md`.

## Target architecture (full detail in docs/DESIGN.md)

Browser → Cloudflare (TLS/proxy) → Traefik on a single EC2 `t4g.small` (k3s) → FastAPI `api` + `retrieval-worker` (KEDA-scaled) ↔ Redis (cache/queue/vector index) ↔ RDS MySQL (source of truth) → Amazon Bedrock (embeddings + LLM).

## Milestones (docs/DESIGN-004-action-plan.md §2)

| Milestone | Status |
|---|---|
| M0 — accounts/tooling setup | In progress — AWS CLI, Titan V2 embeddings, and the $20/month budget are confirmed; Haiku streaming access is unresolved. See `project/BACKLOG.md`. |
| M1 — DD1 Phases 0–3, full system running locally (Docker Compose) | Phase 0, Phase 1 (data layer + API/worker/SSE), and Phase 3 (frontend, wired to the local backend) done and verified end to end. Phase 2 is in progress: provider adapters and model-aware re-embedding are implemented and locally tested; live Haiku streaming, 3 cache layers, rate limiting, and retrieval eval remain. |
| M2 — DD1 Phases 4–7, live on AWS at basel.engineering | Not started |
| M3 — DD2: self-healing, conversational memory, chat UX | Deferred |
| M4 — DD3: production ingestion pipeline (Google Drive/S3/SQS) | Deferred |

## Update this file when

A task changes what exists in the repo (new service, new endpoint, infra provisioned, milestone status changes). Overwrite stale sections — this is a snapshot, not a changelog.
