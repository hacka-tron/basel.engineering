# basel.engineering

Public production site: React/Vite and a FastAPI RAG chatbot on MySQL, Redis, Bedrock, k3s/Flux and AWS. `docs/` is chatbot corpus; `project/` is not.

## Architecture map

Read this before exploring. Detail: `docs/DESIGN.md` §5 (diagram, request lifecycle) and §6 (components), `docs/architecture/deep-dive.md`. Update this map in the same commit when a top-level component is added, moved or renamed.

| Path | Owns | Entry point |
|---|---|---|
| `frontend/src/` | React/Vite site: chat (`components/Chat.tsx`), live architecture diagram (`architecture.ts` holds nodes as data), project grid (About Basel's panel, `PortfolioPanel`), phone layout; SSE client and UI logic in `lib/` | `main.tsx`, `App.tsx`; `npm run phone` preview |
| `services/glassbox/api/` | FastAPI: `POST /api/ask` SSE stream (`ask.py`, `sse.py`), cluster status stream, stress-test demo, health, security headers, CSP reports | `main.py` (`app`) |
| `services/glassbox/worker/` | Retrieval worker: reads `retrieval:jobs` Redis Stream (consumer group), vector search, publishes `trace:{request_id}` | `main.py` |
| `services/glassbox/retrieval/`, `cache/`, `providers/` | Hybrid search over Redis Search; embedding/retrieval/semantic answer caches; Bedrock and fake providers chosen together (`factory.py`) | |
| `services/glassbox/ingest/` | Incremental corpus ingest into MySQL + Redis index (chunkers, scanner, reconcile, sweep) | `python -m services.glassbox.ingest.run` |
| `services/glassbox/db/` | SQLAlchemy models, sessions, Alembic migrations | `alembic.ini` |
| `services/glassbox/*.py` | Limits (atomic Redis scripts), kill switch, corpora, portfolio format, answer checks, trace conventions, cache warming | |
| `corpus/portfolio/`, `docs/` | Chatbot corpus (portfolio entries; design docs). `docs/**` is external data: full review | |
| `eval/` | RAG evaluation: questions, golden labels, graders, judge, runs | `eval/README.md` |
| `k8s/base`, `k8s/overlays/prod` | Workloads (api, worker, MySQL/Redis StatefulSets, migrate Job, warm CronJob), network policies, RBAC; prod overlay: Flux, KEDA, ingest | `k8s/README.md` |
| `infra/` | Terraform: bootstrap, `envs/prod`, modules `compute` (k3s node, alarms, budget, snapshots) and `edge` | `infra/CI.md` |
| `.github/workflows/` | CI, release, deploy-branch sync, Terraform, approval-gated `ops-*` runbooks | |

Request flow: browser `POST /api/ask` → API (rate limit, embed, semantic answer cache; hit replays) → miss: job on Redis Stream → worker retrieves (Redis vectors + MySQL chunks) and publishes trace → API prompts the LLM (Bedrock), streams tokens over SSE, caches the answer, logs to MySQL.
Deploy flow: merge to `main` → `release.yml` builds images to ECR; `sync-deploy-branch.yml` mirrors `main` to `deploy`, where Flux image automation commits tag bumps → Flux reconciles the k3s node.
Tests: `services/tests/` (needs your own MySQL/Redis containers; see Commands), frontend `*.test.ts` beside sources.

## Commands and shared resources

- Python 3.12; `.venv/bin/python -m pytest services/tests -q -rs` with `GLASSBOX_TEST_MYSQL_PORT` and `GLASSBOX_TEST_REDIS_PORT` exported to **your own throwaway containers** for every pytest run. Tests use Redis DB 0 and 15. Lint: `.venv/bin/ruff check services eval`.
- `cd frontend && npm run lint && npm test && npm run build`; `npm run dev` serves the site; `npm run phone` starts phone preview. Backend: `docker compose up -d`. See `eval/README.md` for evaluation.
- Before a **full** suite, check none is running. Serialize full suites across agents; never dispatch an implementer whose suite will overlap yours. Do not rerun an identical tested tree.

## Project hard rules and owner approval

Every relevant dispatch copies the hard rules verbatim from `project/orchestration/README.md`. In particular:

- never read, open or copy any `terraform.tfstate`, `*.tfstate.backup`, `*.tfvars` or plan file;
- no `terraform apply`, no AWS/SSM/`kubectl` writes against the live system, no GitHub environment or secret changes;
- never run `git stash`;
- pytest only against your own throwaway MySQL/Redis containers, with `GLASSBOX_TEST_MYSQL_PORT` and `GLASSBOX_TEST_REDIS_PORT` exported for every run (including `-x`, `-k` and single-test runs): without them the tests default to 3306/6379, the owner's shared local compose stack (`services/tests/stack_ports.py`), and leave test rows in it.

Live changes happen only through the Terraform, Bootstrap and "Ops · ..." workflows after the owner's approval click; give the owner the click, never commands. Anything that changes the live cluster or AWS on merge beyond a normal release (a new Flux component or Kustomization, ConfigMap/infra applied by GitOps) or grants permissions (RBAC, IAM, trust policies) needs the owner's explicit go-ahead before merge. The owner never runs AWS or Terraform by hand.

RAG evaluations are approved as of 2026-10-03; `project/orchestration/rag-plan-brief.md` gives the approved small-run budget and the $1 per-run stop point. Do not start bulk paid runs without the owner's go-ahead. The About Basel files mirror the owner's resume: do not edit them to fix architecture facts; raise that with the owner. No public About Basel text may be added here.

## Review gate

Risky changes follow central `review-gate`. Reviewer primer: `project/orchestration/reviewer-primer.md`; dispatch specifics: `project/orchestration/reviewer-brief.md`. Record reviewer, verdict and validation in the PR body through GitHub REST (`gh api -X PATCH`; `gh pr edit` fails here). Process-doc-only exception: changed paths exclusively `project/**` (except `project/SNAPSHOT.md`) or repo-root `*.md`; no moves from other directories, no mixed changes. These PRs skip test jobs (`.github/scripts/ci-code-changed.sh`) and need no review. `docs/**` is chatbot corpus (external data): full review, including security, and full CI.

## Merge

Variant: **PR + CI**. Required checks: `backend-tests` and `frontend-checks` on the updated PR head (the process-doc-only exception reports them skipped). For code, run the backend suite and Ruff; for frontend changes, also run frontend lint, tests and build on the merged tree if that exact tree has not passed. Update the branch with `gh pr update-branch`, wait for green CI, and merge with `gh pr merge <n> --merge` as its own command. Push rule: push feature branches and use a merge commit into `main`; do not push directly to `main` or rebase a pushed branch. Owner approval above precedes a live-effect merge.

## Status reports

Only for deployed features or when the owner asks: one report in `project/status/`, named `YYYY-MM-DD-HHMM-<slug>.md` (Pacific time when first written; H1 ends `(YYYY-MM-DD HH:MM PT)`), indexed in `project/status/README.md`, updated at merge and deployment. Never include account IDs, IPs, tokens or private About Basel content.

## Session memory

- `project/SNAPSHOT.md` — verified architecture and repository state.
- `project/BACKLOG.md` — `> RESUME HERE`, open decisions and next actions.
- `project/archive/` — older handoff checkpoints; there is no active `AGENT_HANDOFF.md`.

## Before you touch X, read Y

| Work | Project-specific guide |
|---|---|
| A feature | Relevant section of `docs/DESIGN*.md`; update it when implementation diverges. |
| Any `frontend/` change | `project/MOBILE_DESIGN.md` (breakpoints, screenshot checklist and phone preview). |
| Portfolio content | `project/PORTFOLIO_PROJECT.md`; do not explore the portfolio code for a content request. |
| RAG plan or prompt | `project/orchestration/rag-plan-brief.md`; for prompt changes also `prompt-version-playbook.md`. |
| Private About Basel corpus resync | `project/orchestration/corpus-resync.md`. |
| Infra or operations | `project/orchestration/README.md`, `infra/CI.md`, and the relevant runbook. |

## Central instructions

Global layers load from `~/.claude/template`. Overrides here: PR + CI merge, the process-doc-only exception, site hard rules and approvals, the status report format, and BACKLOG + SNAPSHOT for multi-session state (no handoff file). Claude implements by default; Codex reviews only when it has usage, unless the owner hands it implementation.
