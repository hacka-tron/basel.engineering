# SNAPSHOT

Architecture and repo-state blueprint. Read this first when starting a new session — it should make scanning the repo unnecessary for orientation.

**Last updated:** 2026-09-29

## What exists right now

- **Design docs** (`docs/DESIGN.md`, `docs/DESIGN-002-followups.md`, `docs/DESIGN-003-ingestion.md`, `docs/DESIGN-004-action-plan.md`): fully specify the target system ("Glassbox" — a RAG portfolio chatbot with a live architecture diagram) and the build sequencing. Cloudflare (not CloudFront/S3) is the edge/TLS layer throughout — see DD1 §10.3.
- **Phase 0 is done and merged to `main`** (pushed to origin). Repo guardrails (ruff, pre-commit, detect-secrets), a FastAPI skeleton (`services/glassbox/api/`: `main.py`, `db.py`, `cache.py`) with `/healthz` and `/readyz`, and a working `docker-compose.yml` (MySQL 8, `redis-stack-server`, the API) all exist and are verified — `docker compose up --build` brings up all three containers healthy, and `/readyz` returns `200 {"mysql":true,"redis":true,"ready":true}` against the real stack.
- **No AWS resources provisioned.** No Terraform written. AWS CLI not yet configured locally.
- **Domain:** `basel.engineering`, DNS on Cloudflare (proxied). Nothing deployed there yet.
- **Skills installed in this repo** (`.agents/skills/`): the `claude-mem`, `impeccable`, `task-observer`, and `omniroute` suites, plus the built-in `superpowers` set used to write the design docs and plans.
- **Multi-model orchestration pipeline in use as of this session:** Claude (architect/orchestrator, writes blueprints + reviews), Codex CLI (`gpt-6-sol` under a ChatGPT Pro login — implementation), Gemini via the `agy` (Antigravity) CLI (`gemini-3.8-flash-medium` — spec/quality review). See `project/BACKLOG.md` for what worked and what didn't.

## Target architecture (full detail in docs/DESIGN.md)

Browser → Cloudflare (TLS/proxy) → Traefik on a single EC2 `t4g.small` (k3s) → FastAPI `api` + `retrieval-worker` (KEDA-scaled) ↔ Redis (cache/queue/vector index) ↔ RDS MySQL (source of truth) → Amazon Bedrock (embeddings + LLM).

## Milestones (docs/DESIGN-004-action-plan.md §2)

| Milestone | Status |
|---|---|
| M0 — accounts/tooling setup | In progress — see `project/BACKLOG.md` |
| M1 — DD1 Phases 0–3, full system running locally (Docker Compose) | Phase 0 done. Phase 1 (schema/migrations, ingestion, full SSE contract) not started. |
| M2 — DD1 Phases 4–7, live on AWS at basel.engineering | Not started |
| M3 — DD2: self-healing, conversational memory, chat UX | Deferred |
| M4 — DD3: production ingestion pipeline (Google Drive/S3/SQS) | Deferred |

## Update this file when

A task changes what exists in the repo (new service, new endpoint, infra provisioned, milestone status changes). Overwrite stale sections — this is a snapshot, not a changelog.
