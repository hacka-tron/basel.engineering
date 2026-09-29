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
- **DD1 Phase 3a (frontend skeleton) is done and merged to `main`** — a static, unwired React+TS skeleton (`frontend/`, Vite + React 19 + TypeScript + Tailwind v4 + `@xyflow/react`), not yet wired to the backend SSE endpoint:
  - Desktop layout: header, chat panel with suggested-question chips (`frontend/src/components/Chat.tsx`), and a live-styled (idle-state) architecture diagram of all 10 pipeline nodes (`frontend/src/components/ArchitecturePanel.tsx`, `frontend/src/architecture.ts`, matching DD1 §8's `NodeId` union and the §5.1 flow topology), plus a placeholder `StatsBar`.
  - Mobile layout (below Tailwind's `md`/768px breakpoint): chat takes full width, the diagram collapses to a horizontal "pipeline strip" of idle node dots (`frontend/src/components/PipelineStrip.tsx`) above the chat input, and a "View architecture" button opens the full diagram as an accessible bottom sheet (Escape-to-close, focus moved in on open and restored to the trigger on close). Only one `ArchitecturePanel`/React Flow instance is ever mounted at a time — desktop vs. sheet render is driven by `frontend/src/hooks/useMediaQuery.ts`, not CSS-only hiding (CSS-hiding alone left a permanently-mounted-but-invisible React Flow instance on mobile, plus a second live instance whenever the sheet was open — fixed during Phase 3a Task 3's verification pass).
  - Design tokens in `frontend/src/index.css` (`@theme`): dark theme, one accent color (`cyan`, reserved for "active" states), JetBrains Mono throughout. Two Tailwind v4 gotchas documented there in comments: `@theme inline` var-to-var aliasing resolves empty (hardcode hex values instead), and custom color tokens must avoid Tailwind's reserved scale keys (`base` collided with `--text-base`, renamed to `canvas`).
  - Verified in a real browser (desktop ~1440px, mobile ~500-614px, and both sides of the 768px breakpoint) via `claude-in-chrome` — no console errors/warnings beyond a pre-existing, expected React Flow attribution notice.
- **Real `about_me` corpus content exists** (`corpus/about-me/*.md`: `bio.md`, `microsoft.md`, `google.md`, `projects.md`, `skills.md`), sourced from Basel's actual resumes. No `bullet-bank.md` (no source doc to export from) and no Glassbox entry in `projects.md` yet (not deployed).
- **No AWS resources provisioned.** No Terraform written. AWS CLI not yet configured locally.
- **Domain:** `basel.engineering`, DNS on Cloudflare (proxied). Nothing deployed there yet.
- **Skills installed in this repo** (`.agents/skills/`): the `claude-mem`, `impeccable`, `task-observer`, and `omniroute` suites, plus the built-in `superpowers` set used to write the design docs and plans.
- **Multi-model orchestration pipeline in active use:** Claude (architect/orchestrator, writes blueprints + reviews), Codex CLI (`gpt-6-sol` under a ChatGPT Pro login — implementation), review via Gemini (`gemini-3.8-flash-medium` through `agy`) **or a Claude subagent** — Gemini's quota is currently exhausted (resets ~2026-10-07), so reviews are running through Claude in the meantime. See `project/BACKLOG.md` for what worked and what didn't.

## Target architecture (full detail in docs/DESIGN.md)

Browser → Cloudflare (TLS/proxy) → Traefik on a single EC2 `t4g.small` (k3s) → FastAPI `api` + `retrieval-worker` (KEDA-scaled) ↔ Redis (cache/queue/vector index) ↔ RDS MySQL (source of truth) → Amazon Bedrock (embeddings + LLM).

## Milestones (docs/DESIGN-004-action-plan.md §2)

| Milestone | Status |
|---|---|
| M0 — accounts/tooling setup | In progress — see `project/BACKLOG.md` |
| M1 — DD1 Phases 0–3, full system running locally (Docker Compose) | Phase 0 and Phase 1 (data layer + API/worker/SSE) both done and verified end to end. Phase 2 (real Bedrock providers, 3 cache layers, rate limiting, retrieval eval) not started. Phase 3 (frontend) not started. |
| M2 — DD1 Phases 4–7, live on AWS at basel.engineering | Not started |
| M3 — DD2: self-healing, conversational memory, chat UX | Deferred |
| M4 — DD3: production ingestion pipeline (Google Drive/S3/SQS) | Deferred |

## Update this file when

A task changes what exists in the repo (new service, new endpoint, infra provisioned, milestone status changes). Overwrite stale sections — this is a snapshot, not a changelog.
