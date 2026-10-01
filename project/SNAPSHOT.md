# SNAPSHOT

Architecture and repo-state blueprint. Read this first when starting a new session: it should make scanning the repo unnecessary for orientation. It is a snapshot, not a changelog: overwrite stale sections. History lives in `project/AGENT_HANDOFF.md` and `project/status/`.

**Last updated:** 2026-10-01 (session wrap-up after the node memory incident, ops runbooks and the mobile pass)

## Live state (2026-10-01)

- `https://basel.engineering` is live and serving. Merging to `main` deploys hands-off: GitHub Actions builds and pushes `build-N` to ECR, Flux Image Update Automation commits the tag to the `deploy` branch, and ordered Flux Kustomizations roll it out (`flux-system` → `app-ready` → `ingest`).
- **Node memory:** compressed swap is on (`/dev/zram0`, ~920 MB uncompressed capacity, lzo-rle, priority 100; EBS `/swapfile` overflow at priority -2). After the 2026-09-30 incident fix: memory PSI about 3% (`some` avg300), about 357 MiB available.
- **KEDA is installed but suspended:** its Flux Kustomizations (`keda`, `keda-scaling`) and the `keda` HelmRelease are suspended, and the Deployments in the `keda` namespace are scaled to 0. The HelmRelease is in a failed state from the incident's install timeout. The retrieval worker runs at one replica and nothing autoscales. Free memory is below the stress test's 512 MiB gate, so the stress-test button plays the simulation.
- **`warm-answers` CronJob is running** (every 2h at :17 UTC, at most 10 LLM answers/day). It was suspended during the incident; Flux re-applied the manifest with `suspend: false`, so a lasting suspend would have to be made in Git.
- **Operations are push-button.** The owner never runs AWS or Terraform by hand any more: "Ops · ..." workflows for node operations, `terraform.yml` for `infra/envs/prod`, `bootstrap.yml` for `infra/bootstrap`. Agents give the owner approval clicks, not commands.

## What exists right now

### Backend (`services/glassbox/`)

- **API** (`api/`, FastAPI): `POST /api/ask` streams the SSE trace contract (`stage`/`retrieval`/`token`/`done`/`error`; DD1 §8) with a 15s `: ping` heartbeat and server-side stop on client disconnect (`mode='stopped'`). Also `GET /api/demo/capacity`, `POST /api/demo/load`, `GET /api/cluster/stream`, `/healthz`, `/readyz`. Serves the built frontend.
- **Cluster stream** (`api/cluster.py`, DESIGN §9.5 "Bounded cost"): one shared pod watch and one Redis backlog poller per api process (`ClusterHub`), fanned out to per-client bounded queues; snapshot then `synced`; caps 100 streams per process and 5 per IP hash (503/429 + `Retry-After`); 10-minute max lifetime ending in `reconnect`; 15 s `: ping`. Limits are `GLASSBOX_CLUSTER_STREAM_*` ConfigMap keys with the same defaults in code. The browser (`lib/clusterStream.ts`) reconnects itself with jittered exponential backoff (first retry 2.5–5 s, up to 2 min) and keeps the last pod dots on screen.
- **Pipeline:** per-IP rate limit (10 per 10 min, salted HMAC IP hash) → follow-up rewrite (Nova Lite, ≤60 tokens, follow-ups only) → embedding cache → Titan V2 embedding (512-dim) → semantic answer cache (cosine ≥0.95, first questions only, 15s anti-stampede lock) → Redis Streams queue `retrieval:jobs` → `retrieval-worker` (KNN on `idx:chunks`, filtered by corpus and embedding-model tag; retrieval and chunk caches; MySQL) → Nova Lite ConverseStream (≤400 tokens) → answer cache write (never for abstentions or empty answers) → `queries` log.
- **Cost controls:** daily LLM budget in quarter-units (answer 4, rewrite 1; default cap 100 answers/day), `retrieval_only` mode at the cap, Redis kill switch `glassbox:kill:disable_llm`.
- **Grounding:** prompt `v13`. Non-code sources are labelled per unit with `[PLANNED, not built yet]` when they match `_PLANNED_SOURCE_SIGNAL` (`api/ask.py`); code/manifests/infra are never labelled; live infra (KEDA, k3s, Terraform, Flux) is not on the keyword list. Abstentions set `done.abstained`.
- **Conversation:** stateless server; the client sends up to 6 settled messages / 4,000 chars; the server re-bounds it. Alembic revisions `0001`–`0004` (`queries.turn_index`, `rewritten_query`, `mode='stopped'`).
- **Ingestion** (`ingest/`): content baked into the image; incremental by content hash and embedding model; path denylist and secret heuristics; Markdown/Terraform/YAML/code chunkers. Runs as the `ingest` Job after each rollout, and ends with an answer warm-up. After a full scan it checks for stale documents (files gone, per corpus and model): the Job runs this in `report` mode (logs only; `GLASSBOX_INGEST_SWEEP=apply` deletes, with a zero-file guard and a 30% limit). `--dry-run` and `--clear --corpus X [--yes]` are operator commands (`ingest/sweep.py`).
- **Answer warm-up** (`warm.py`): asks the suggested questions through the API so their answers stay cached; shared daily cap `GLASSBOX_WARM_DAILY_LLM_CAP=10`.
- **Providers:** `GLASSBOX_PROVIDER=fake|bedrock`; fake is the local/CI default and makes no network calls. Production: Titan Text Embeddings V2 + Nova Lite (Claude Haiku streaming is blocked by the account's first-time-use form; do not submit it for the owner).
- **Eval:** `eval/run_eval.py` (retrieval eval v2, RAG quality plan phase 2): k=8 like the worker, file-level recall@5/MRR (continuity) plus recall@8, chunk-level recall@8/MRR (gold snippets) and noise@8 (tests/plans share), per corpus and category. 30 questions until `eval/golden.yaml` lands. Stored Titan baseline (v1 format, stale fingerprint) recall@5 0.8667, MRR 0.6917. Manual, not in CI; metric unit tests and a fake-provider end-to-end test run in CI.
- **Answer eval (RAG quality plan phase 1, #97):** `eval/golden.yaml` (75 cases: fact, planned, live, unanswerable, multi-turn, injection), free deterministic graders in `eval/graders.py`, and `eval/run_answers.py` (in-process answers, JSONL to `eval/runs/`). Dataset validation and grader tests run in CI; no paid run yet.

### Frontend (`frontend/`, Vite + React 19 + TypeScript + Tailwind v4 + React Flow)

- Desktop: header (name, topic nav About Basel / About This System, envelope Contact, GitHub), chat, live 11-node architecture diagram (hover previews a node, click asks about it on About This System, a click on empty diagram space or Escape deselects it without stopping a streaming answer), footer stats (latency as bare `ms` with a tooltip, queries served with correct singular "1 query served", tiger/bunny capacity icon, Stress test control).
- Chat: separate persisted conversation per topic (`localStorage`, 7-day expiry), ask box stays enabled while an answer streams (sending stops the current answer first), Up-arrow recall of the topic's last question (`lib/askInput.ts`), Retry under the latest failure reply that replaces the failed attempt and waits out a rate limit (`lib/chatRetry.ts`; a reload mid-retry still shows the failure reply and Retry; on phones focus goes to the retried question), Stop button, one polite live region for screen readers ("Answer stopped.", "Retry is available now.", nothing for a stop-and-send; `lib/chatAnnouncement.ts`), 45s idle watchdog, smart auto-scroll with "Jump to latest", friendly non-blaming error replies (`lib/errorReplies.ts`), playful "I don't know" replies swapped in only for the exact canonical abstention (`lib/idkReplies.ts`; history keeps the canonical sentence), playful daily-budget replies above the sources on a `retrieval_only` answer or a `budget_exhausted` failure (`lib/budgetReplies.ts`; stored with a `budget` flag that keeps Retry off them; history keeps the canonical sentence), "Searched for: ..." under rewritten follow-ups.
- Mobile (below 768px, or a phone held sideways: at most 500px tall and under 1024px wide; rules in `project/MOBILE_DESIGN.md`): Chat | Diagram switch above the ask box; the diagram replaces the chat in place (portrait graph, capped details that are open exactly while a component is selected and otherwise a locked bar ("Select a component for details", `aria-disabled`); the details chevron, a tap on empty diagram space, or Escape deselect; Back/Escape return (Escape deselects first); status "Select a component"); every stress-test tap shows the diagram; focus mode hides header and footer while typing; footer "+" New chat on the right beside the capacity icon (long-press tooltips via `useLongPressTooltip`); header name shortens to "Basel A-R" by measurement (`lib/headerName.ts`, `useFullNameFits`); 13px messages and 13px ask box, with `maximum-scale=1` added on iOS only to stop focus zoom; one-row header (name, envelope Contact "Copy email", GitHub) with `py-2`; topic chips ("Asking about") above the ask box in Chat view only, unmounted in Diagram view (#78). Landscape phones (at most 500px tall, under 1024px wide, 896x414 included) keep the phone layout with compact header/footer, chips beside the ask box, the header hidden in Diagram view, a three-row landscape graph and the details beside the diagram (the whole graph fits beside them: the zoom floor gives way down to 0.54 at 568x320) (`phone-landscape:` variant; `md` redefined in `index.css`, queries in `lib/layout.ts`; PR #100 open, proposed).
- Diagram refits on container resize (`lib/diagramFit.ts`), never on trace updates. Only one React Flow instance is mounted.
- `npm test` (Node's built-in runner) covers the `lib/` helpers; CI runs lint, test and build.
- **Phone preview:** `npm run phone` → http://localhost:5230/phone-preview.html shows five portrait phone frames (393, 375, 360, 320, 280 wide) and four landscape ones (667x375, 740x360, 896x414, 568x320) against the live API. Use it (and comparison pages for design choices) instead of a shrunk browser window.

### Kubernetes (`k8s/`, k3s single node)

- `app`: `api` (1 replica, `maxSurge: 0`), `retrieval-worker` (no fixed `replicas`; KEDA's HPA owns it when KEDA is on), `migrate` Job, `ingest` Job (overlay, after `app-ready`), `warm-answers` CronJob. `wait-for-migrations` initContainers gate api/worker on the Alembic head.
- `data`: MySQL 8.0 and redis-stack-server 7.2 StatefulSets. NetworkPolicies default-deny in `app`; data stores accept only listed app pods (and `keda` for Redis).
- `keda`: KEDA 2.21 via Flux HelmRelease, `ScaledObject` 1→3 on `retrieval:jobs` lag (`keda-scaling`). **Suspended and scaled to 0** (see Live state).
- RBAC: `api` ServiceAccount can watch pods in `app` and list nodes / node metrics (capacity gate). Capacity gate: one node, `MemoryPressure=False`, ≥512 MiB live free memory; 5-minute shared cooldown (`demo:load:lock`).

### Infrastructure (`infra/`, Terraform)

- `infra/envs/prod` (S3 backend, native lockfile): modules `network`, `compute` (t4g.small, EIP, IAM, `ignore_changes = [ami]`, zram SSM document + association in `zram.tf`), `secrets`, `registry` (ECR), `ops` (nine `glassbox-ops-*` SSM documents), `edge` (Cloudflare A record + `/api/*` cache bypass).
- `infra/bootstrap`: state bucket (versioned, encrypted, TLS-only policy, `prevent_destroy`), GitHub OIDC provider, roles `glassbox-ci`, `glassbox-ci-plan`, `glassbox-ci-release`, `glassbox-ops-read`, `glassbox-ops`, `glassbox-bootstrap-plan`, `glassbox-bootstrap`. Its state is in S3 at `bootstrap/terraform.tfstate` (migrated by the owner); CI's Terraform roles are limited to `envs/prod/*`.

### CI/CD and operations (`.github/workflows/`)

- `ci.yml` (backend tests with MySQL/Redis services, frontend lint/test/build; required on `main`), `release.yml` (native arm64 build → ECR, `build-N` tags; a manual run must build `main`'s current head, `.github/scripts/release-provenance.sh`), `sync-deploy-branch.yml` (`main` → `deploy`, re-fetch/re-merge/retry up to 5 times on a rejected push, never forced, `.github/scripts/sync-deploy-branch.sh`), `terraform.yml` (plan on PR, `terraform-prod`-gated apply).
- `bootstrap.yml`: plan on PRs (`bootstrap-plan`), approval-gated apply from `main` (`bootstrap`) only if the re-plan's SHA-256 fingerprint matches the reviewed plan. First live use applied the zram IAM fixes (#64, #66).
- Ops runbooks: eight "Ops · ..." wrappers (Diagnose, Reboot node, Restart deployment, Flux suspend or resume, Flux reconcile, KEDA on or off, Warm-up CronJob suspend or resume, Apply zram) calling reusable `ops.yml`. Diagnose: `ops-read`, no approval, redacted output. Everything else: owner approves `ops`, diagnose before and after. Reboot proves itself by boot-ID change. Runbook table: `infra/CI.md` "Runbooks"; incident playbook: `k8s/README.md` "Incidents".
- GitHub environments: `terraform-plan`, `terraform-prod`, `release`, `ops-read`, `ops`, `bootstrap-plan`, `bootstrap`.

### Process

- Claude orchestrates and implements (Opus/Sonnet/Haiku subagents, parallel worktrees). Codex (`gpt-6-sol`) is the review gate when it has usage; while Codex is out of usage, an Opus subagent reviews with the same primer brief (`project/orchestration/reviewer-primer.md`). Review-round cap, auto-merge after review and green CI, and the other standing owner instructions are in `project/CLAUDE.md` "Working style" and `project/orchestration/README.md`.
- Owner-facing reports per change in `project/status/` (index in its README).
- Docs: `docs/architecture/deep-dive.md` (ingested into About This System; keep it accurate), `docs/DESIGN*.md` (design; some drift, see BACKLOG).

## Target architecture (full detail in docs/DESIGN.md)

Browser → Cloudflare (TLS/proxy) → Traefik on one EC2 `t4g.small` (k3s) → FastAPI `api` (also serves the frontend) + `retrieval-worker` (KEDA 1–3 when KEDA is on; one replica while it is suspended) ↔ in-cluster Redis (caches, queue, vector index) and MySQL (source of truth) → Amazon Bedrock (Titan V2 embeddings, Nova Lite answers). GitHub Actions builds and pushes; Flux deploys.

## Milestones (docs/DESIGN-004-action-plan.md §2)

| Milestone | Status |
|---|---|
| M0 — accounts/tooling | Done, except Anthropic Haiku streaming (blocked by the first-time-use form; Nova Lite used instead). |
| M1 — DD1 Phases 0–3, local system | Done. |
| M2 — DD1 Phases 4–7, live on AWS | Phases 4–6 done and live (Terraform, k3s, CI/CD, Flux GitOps, KEDA stress test with capacity gate; KEDA currently suspended). Phase 7 polish (load-test numbers, README screenshots) not started. |
| M3 — DD2: self-healing, conversational memory, chat UX | Conversational chat, stream resilience (heartbeat, Stop, watchdog, auto-scroll) and the live chat UX leftovers (typing while streaming, Up-arrow recall, Retry) done. Remaining: ASG-based node recovery, Cloudflare streaming check, TTFT logging. |
| M4 — DD3: production ingestion pipeline (Google Drive/S3/SQS) | Not started. |

## Update this file when

A task changes what exists in the repo or what runs live (new service, endpoint, infra, live state such as KEDA coming back, milestone status). Overwrite stale sections.
