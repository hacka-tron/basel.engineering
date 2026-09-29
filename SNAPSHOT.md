# SNAPSHOT

Architecture and repo-state blueprint. Read this first when starting a new session — it should make scanning the repo unnecessary for orientation.

**Last updated:** 2026-09-28

## What exists right now

- **Design docs** (`DESIGN.md`, `DESIGN-002-followups.md`, `DESIGN-003-ingestion.md`, `DESIGN-004-action-plan.md`): fully specify the target system ("Glassbox" — a RAG portfolio chatbot with a live architecture diagram) and the build sequencing. Cloudflare (not CloudFront/S3) is the edge/TLS layer throughout — see DD1 §10.3.
- **Implementation plan** (`docs/superpowers/plans/2026-09-28-phase0-repo-scaffold.md`): task-by-task plan for repo guardrails + a FastAPI skeleton + Docker Compose stack. **Not yet executed** — no application code exists yet.
- **No AWS resources provisioned.** No Terraform written. AWS CLI not yet configured locally.
- **Domain:** `basel.engineering`, DNS on Cloudflare (proxied). Nothing deployed there yet.
- **Skills installed in this repo** (`.agents/skills/`): the `claude-mem`, `impeccable`, `task-observer`, and `omniroute` suites, plus the built-in `superpowers` set used to write the design docs and plans.

## Target architecture (full detail in DESIGN.md)

Browser → Cloudflare (TLS/proxy) → Traefik on a single EC2 `t4g.small` (k3s) → FastAPI `api` + `retrieval-worker` (KEDA-scaled) ↔ Redis (cache/queue/vector index) ↔ RDS MySQL (source of truth) → Amazon Bedrock (embeddings + LLM).

## Milestones (DESIGN-004-action-plan.md §2)

| Milestone | Status |
|---|---|
| M0 — accounts/tooling setup | In progress — see `BACKLOG.md` |
| M1 — DD1 Phases 0–3, full system running locally (Docker Compose) | Not started (plan written, not executed) |
| M2 — DD1 Phases 4–7, live on AWS at basel.engineering | Not started |
| M3 — DD2: self-healing, conversational memory, chat UX | Deferred |
| M4 — DD3: production ingestion pipeline (Google Drive/S3/SQS) | Deferred |

## Update this file when

A task changes what exists in the repo (new service, new endpoint, infra provisioned, milestone status changes). Overwrite stale sections — this is a snapshot, not a changelog.
