# Glassbox Design Doc 004: Action Plan & Deployment Deviations

| | |
|---|---|
| **Status** | Draft v1 |
| **Owner** | Basel |
| **Last updated** | 2026-09-28 |
| **Builds on** | `DESIGN.md` ("DD1"), `DESIGN-002-followups.md` ("DD2"), `DESIGN-003-ingestion.md` ("DD3") |

---

## 1. Summary

DD1, DD2 and DD3 specify the system. This document is the execution plan: what gets built in what order, what changes about the AWS setup given real-world constraints (an AWS account currently on the Free plan, a domain already pointed at Cloudflare), and a checklist of the setup work that isn't code — account settings, console clicks, DNS — split from the work that gets handed to Claude Code.

**Two real deviations from the written designs, decided in this doc:**

1. **Cloudflare replaces CloudFront + S3.** DD1 §5/§10.3 specify AWS CloudFront + S3 (with Origin Access Control) as the edge/TLS/static-hosting layer. The domain is already on Cloudflare, which does the same job (TLS, edge proxy, origin protection) for free with less Terraform surface area, and natively supports proxying the apex domain (`basel.engineering`), which CloudFront + Route 53 does not do as simply. CloudFront was never the differentiating part of this system — see section 3.
2. **Build order is DD1 → DD2 → DD3, not simultaneous.** Get a real, deployed, answering site first, then add DD2 and DD3 as follow-on milestones.
   Status: the baseline is live, and DD2's conversational chat and chat UX (including Stop and stream heartbeats) have shipped. DD2's self-healing node and the DD3 Google Drive ingestion pipeline are not built yet.

---

## 2. Milestone overview

| Milestone | Maps to | Outcome |
|---|---|---|
| M0 | — (new) | Accounts, tooling, and repo ready to build |
| M1 | DD1 Phases 0–3 | Full system running locally via Docker Compose: chat, RAG, streaming, live architecture panel, mock and real data |
| M2 | DD1 Phases 4–7 (edge adjusted) | Live at `basel.engineering` on AWS: k3s on EC2, RDS, Redis, Bedrock, KEDA autoscaling, CI/CD |
| M3 (shipped part) | DD2 | Conversational memory, chat UX polish, streaming heartbeats and server-side Stop |
| M3 (not built yet) | DD2 | Self-healing (ASG), post-deploy streaming check in CI |
| M4 | DD3 | Production content pipeline: author "About Basel" in Google Docs, S3/SQS event pipeline, reconciliation, blue-green re-embedding |

Each milestone ends with something real: M1 ends with a working demo on your laptop; M2 ends with a public URL a recruiter can visit; M3 and M4 are hardening and workflow improvements layered onto a system that's already live.

---

## 3. Edge/CDN: Cloudflare

DD1 and DD2 have been updated in place to specify Cloudflare (already managing DNS for `basel.engineering`) as the TLS/edge layer, replacing CloudFront + S3 — see DD1 §5.1, §5.3, §6.1, §10.3, §11, §14, and DD2 §7. No separate deviation to track here; the source designs are current.

The short version: Traefik on the EC2 node serves both the built frontend and `/api/*` directly (single origin, no S3), the security group allowlists Cloudflare's published IP ranges instead of CloudFront's prefix list, and a Cloudflare Cache Rule bypasses caching on `/api/*` so the SSE stream isn't buffered (DD2 §7.3).

---

## 4. Milestone 0: Accounts, domain, and tooling

Owner tasks — manual, console/CLI work that can't be delegated to Claude Code.

- [x] Commit the DESIGN docs to git.
- [x] Install and configure the AWS CLI locally. Authentication was verified; needed for local Bedrock calls in M1 Phase 2 and for Terraform later.
- [x] Confirm 512-dimension Titan Text Embeddings V2 access in `us-east-1`. A small real call returned a 512-dimensional vector on 2026-09-29. A prior nonstreaming Haiku call succeeded; a later `converse_stream` attempt reported missing model use-case details, so streaming access still needs verification before Phase 2 acceptance.
- [x] Set up AWS Budgets before deploying resources: `Glassbox-Monthly` is set to $20/month, per the owner's updated limit. The previous session recorded actual-spend alerts at 50/80/100% and a forecast alert; the budget amount was verified again by CLI.
- [ ] **Decision checkpoint before M2**: AWS account is currently on the Free plan, which auto-closes the account after 6 months or when credits run out — this would take the live site down mid-search with no warning beyond the budget alerts. Decide Free vs. Paid before the first `terraform apply` against real AWS resources in M2.
- [ ] Confirm Cloudflare proxy mode (orange-cloud/proxied) is intentional for `basel.engineering`, and that DNS is otherwise untouched (no conflicting records).
- [ ] Local tooling: Docker (for Compose), Node.js, Python 3.12. No Kubernetes tooling needed locally (decided in section 6).

Claude Code can do once the above is done: everything else — repo scaffolding, all application code, Terraform, Kubernetes manifests, CI/CD.

---

## 5. Milestone 1: Local system (DD1 Phases 0–3)

Unchanged from DD1 §17 Phases 0–3, run entirely with Docker Compose (MySQL, Redis, API, worker, fake LLM/embedding providers, then real Bedrock calls once M0's model access is enabled). No AWS resources, no Kubernetes.

| Phase | Work | Done when |
|---|---|---|
| 0 | Repo structure, linting, pre-commit, secret scanning | `pre-commit` passes; repo scaffolded per DD1 §16 minus `infra/modules/edge` |
| 1 | Docker Compose backend, schema/migrations, ingestion for both corpora (DD1's original repo-baked approach, not yet DD3's pipeline), API + worker, full SSE contract | `curl -N` against `/api/ask` streams stage/retrieval/token/done events locally |
| 2 | Real Bedrock providers, 3 cache layers, rate limit + daily budget, retrieval eval with baseline | Answers are cited and correct on the eval set; repeated questions hit the answer cache |
| 3 | Frontend: layout, chat, citations, architecture panel (mock traces, then live backend), mobile pipeline strip, degraded modes | Full question animates end to end on desktop and phone widths against the local backend |

---

## 6. Milestone 2: Live on AWS (DD1 Phases 4–7, edge adjusted)

| Phase | Work | Done when |
|---|---|---|
| 4 | Terraform: `network` (VPC, no NAT), `compute` (EC2 t4g.small + k3s user_data), `database` (RDS MySQL), `secrets` (SSM), `budgets`. **No `edge` module.** Cloudflare DNS record → Elastic IP. Security group scoped to Cloudflare's IP ranges. Traefik serves static frontend + `/api/*`. K8s base manifests, RBAC, NetworkPolicies, manual first deploy | `https://basel.engineering` serves the site and answers questions |
| 5 | KEDA, synthetic load endpoint, cluster stream, pod dots in the UI | Stress test visibly scales workers 1→3 and back |
| 6 | GitHub Actions (GHCR images), Flux bootstrap, frontend deploy step (now: sync built files onto the node instead of S3+CloudFront invalidation — e.g., include the frontend build in the API/Traefik image, or a small `scp`/`rsync` step in the deploy workflow), plan-on-PR, post-deploy Cloudflare streaming check (section 3.3) | Merging to `main` deploys without touching the server |
| 7 | README with screenshots, footer stats, suggested questions tuned, load test numbers recorded | Polish complete |

I did **not** design the exact frontend-delivery mechanism for phase 6 in detail (options: bake the static build into the API container image and let Traefik serve it from a volume, or a tiny separate static-file container in the same pod/deployment) — worth a quick decision when you reach that phase, not now.

Local dev stays exactly as in M1 for the whole project — nothing in M2 changes the Docker Compose loop.

---

## 7. Milestone 3: DD2 features (partly shipped)

Once M2 is live and usable, layer in, in this order:

1. **Self-healing (DD2 §3)** — not built yet. Launch Template + Auto Scaling Group, boot script, health-check timer. High value early since it's the difference between "recruiter hits a dead site" and "site heals itself." The Elastic IP reassociation logic is unchanged by the Cloudflare swap — Cloudflare still just points at a stable IP.
2. **Streaming hardening formalized (DD2 §7)** — the post-deploy CI check is not built yet. Plan: codify the Cloudflare-specific checks from section 3.3 above into the post-deploy CI check.
3. **Corpus authoring guide + validation (DD2 §4)** — not built yet; only relevant if still hand-authoring Markdown files in-repo at this point (i.e., before M4).
4. **Conversational chat + live chat UX (DD2 §5, §6)** — shipped: multi-turn memory, follow-up rewriting, Stop button, localStorage persistence, accessibility. DD2 §7's stream heartbeats and server-side Stop have shipped too.

---

## 8. Milestone 4: DD3 production ingestion pipeline (deferred)

Build when hand-editing Markdown files and redeploying to update "About Basel" content becomes annoying enough to be worth it — likely once the site is live and you're actively updating content during a job search. DD3's own build plan (§20, I-1 through I-6) applies unchanged. This is the biggest remaining chunk of infrastructure work (S3 raw zone, SQS + DLQ, Google Drive connector, KEDA ScaledJob, nightly reconciliation, blue-green re-embedding) and is explicitly scoped as a v2, not a blocker to launch.

---

## 9. Cost model (updated)

DD1 §14's baseline is ~$31–36/month + LLM (already updated there) — CloudFront/S3 were already near-$0 in the original table, so removing them just removes Terraform complexity, not meaningful cost. No Route 53 hosted zone cost either, since DNS stays on Cloudflare.

| Item | Monthly |
|---|---|
| EC2 `t4g.small` (24/7) | ~$12.30 |
| EBS 20 GB gp3 | ~$1.60 |
| Public IPv4 (Elastic IP) | ~$3.65 |
| RDS `db.t4g.micro` single-AZ (24/7) | ~$11.70 |
| RDS storage 20 GB | ~$2.30 |
| Cloudflare (DNS + proxy + TLS) | $0 |
| Bedrock embeddings | pennies |
| Bedrock LLM (capped at 100 answers/day) | ~$1–5, worst case ~$15 |
| **Baseline total** | **~$31 to $36 + LLM** |

Same credit/runway math as DD1 §14 applies — and it's tied directly to the Free-vs-Paid decision flagged in section 4.

---

## 10. Open decisions carried forward

| Decision | From | Status |
|---|---|---|
| AWS account: Free vs. Paid | New | Must decide before M2 (section 4) |
| Cloudflare origin-header injection (Worker) vs. IP-range-only origin protection | New (section 3.1) | IP-range-only is sufficient for M2; revisit if abuse becomes a concern |
| Frontend delivery mechanism on the node (baked into image vs. separate static container) | New (section 6) | Decide at Phase 6 |
| Name: Glassbox or other | DD1 §19 | Owner's call, unchanged |
| Keep RDS after credits run out | DD1 §19 | Revisit based on job-search status, unchanged |
