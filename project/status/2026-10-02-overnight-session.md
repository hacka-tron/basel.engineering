# Overnight session, 2026-10-02: what shipped and what waits on you

**Status:** session summary (no code of its own). Written by Claude (Opus orchestrating Sonnet/Opus subagents, Opus review gate).

## TL;DR

While you slept, 13 PRs merged (#130 to #135, #137 to #143). Each went through the review gate and green CI, and each release deployed hands-off. One PR, **#136 (Ops · Reindex)**, is reviewed and green but **waits for your go-ahead**. It adds a new privileged SSM document, so merging it leads to a Terraform apply you approve. Nothing touched AWS, IAM or the cluster directly.

## What merged

| PR | What it does | Visitor-visible? |
|---|---|---|
| #130 | Docs: HTTPS redirect on, KEDA on hold, the about-me RDS line is intentional | No |
| #131 | A budget-limited turn no longer sends a client-invented sentence as chat history | No (cleaner follow-ups) |
| #132 | Ingest secret scanner recognizes GitHub/Slack/Anthropic/OpenAI/Google/Stripe/JWT/Bearer tokens; two regexes went from quadratic to linear | No |
| #133 | Public `corpus/about-me/` copies deleted (live answers verified citing `private/...`) | No |
| #134 | Cluster-stream test depth plus a strong reference to cancelled upstream tasks | No |
| #135 | DESIGN.md §11 matches reality (Ops runbooks, six OIDC roles, HTTPS 301) | Chatbot answers about security |
| #137 | `queries.ttft_ms`: server-side time to first token (migration 0005) | No |
| #138 | Ingest writes MySQL first, then Redis (no Redis I/O inside the transaction); N+1 queries gone; a failed run's status update can't hide the real error | No |
| #139 | README with live screenshots, an architecture diagram, honest details and a quickstart (M2 Phase 7, README part) | GitHub visitors |
| #140 | A stopped empty reply is left out of history; cluster hub shutdown bounded at 10 s | No |
| #141 | Dead twin-shadowing code removed; `corpus/about-me/` can never be indexed again | No |
| #142 | `--clear` also removes orphan Redis chunk keys (re-checked against MySQL); sweep outcomes stored in `ingestion_runs.notes` (migration 0006) | No |
| #143 | New **Stream check** workflow: after each release (and daily) it checks SSE through Cloudflare (unbuffered, heartbeats, headers, 301), plus `GET /api/version`. It never calls `/api/ask`, so it costs nothing | No |

## Waiting on you

1. ~~**#136 Ops · Reindex**~~ **Done (2026-10-02 morning):** the owner said go; merged, Terraform apply approved and succeeded (SSM document `glassbox-ops-reindex`), live on build-105, Stream check passed.
2. **Alarms and snapshots (#124):** the Bootstrap run and the Terraform apply both succeeded about 04:20 UTC. Still to do: confirm the AWS SNS email, then run **Ops · List snapshots** and **Ops · Diagnose**. Tomorrow, check that the first daily snapshot exists.
3. **Terraform apply prompts for #143 and others:** merges that touch `.github/` or `infra/` start a Terraform run that may wait for your approval with a no-op plan. Approve it or let it lapse.
4. Owner decisions in BACKLOG "Open decisions" are unchanged (AWS Free vs Paid, warm-answers, About This System fallback, chat bubble tightening).

## What review caught tonight (worth knowing)

- #133: the Dockerfile's `COPY corpus/` would have failed any release built without the private checkout. Fixed with a tracked `corpus/README.md` and a test that every COPY source is tracked.
- #132: the existing assignment regex took minutes on long lines (a minified file could stall the ingest Job). Now linear, with timing tests.
- #141: rewritten integration fixtures broke 7 CI tests that skipped locally. Lesson recorded: **Docker is down on this Mac**, so implementers now wait for CI before reporting (added to `~/Coding/template/agent-orchestration.md`).
- #142: a race where `--clear` could delete keys a concurrent ingest had just written. Fixed with a MySQL re-check, and the lock is added in #136.

## Operational notes

- **Single-replica rollouts:** the stream check's dev run hit a 503 while a pod was being swapped. The page fetch now retries, but visitors can see a few seconds of 503 per release. This is known and comes from one replica with `maxSurge: 0`.
- **Docker Desktop on the Mac is unresponsive** (`docker info` hangs). Local MySQL/Redis tests skip; CI covers them.
- Two migrations shipped (0005, 0006). Both add a nullable column, which is instant on MySQL 8.
