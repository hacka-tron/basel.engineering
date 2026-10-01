# Reviewer primer (read this first)

For the review gate: Codex, or an Opus subagent standing in when Codex is out of usage (same brief, same rules). A compact orientation plus the traps earlier review rounds found, so a review starts warm. It is a map, not a spec: the dispatch prompt's requirements win, and anything here that contradicts the code is stale (fix the primer in the same PR). Last mined: 33 Codex results through 2026-09-30.

## System in one screen

- **Request path:** browser, then Cloudflare (TLS/proxy), then Traefik, then FastAPI `api` (`POST /api/ask`, SSE: `stage`/`retrieval`/`token`/`done`/`error`, `: ping` heartbeat every 15 s). `api` enqueues a job on a Redis Stream (`retrieval:jobs`). `retrieval-worker` pods read it, run KNN on the Redis index `idx:chunks`, hydrate chunks from MySQL, and publish trace events. `api` then streams the answer from Bedrock (Titan V2 512-d embeddings, Nova Lite ConverseStream). Follow-ups add a `rewrite` stage. Both processes share one `seq`/`t_ms` convention (`services/glassbox/trace.py`).
- **Caches and limits (Redis):** embedding, retrieval, chunk, semantic answer cache (cosine >= 0.95, scoped by corpus + corpus version + model + prompt version, miss lock with NX/PX). Rate limit 10 per 10 min per salted IP hash; daily LLM budget via Lua scripts (answers 4 quarter-units, rewrites 1, so rolling deploys keep counting). Kill switch `disable_llm`. Bump the prompt version when prompt or grounding text changes, or old answers replay.
- **Corpora:** `about_me` and `about_system`. The system corpus is this repo's own docs, so docs edits change answers and must reach the image (release path filter, then ingest).
- **Platform:** one `t4g.small` EC2 running k3s, about 1.84 GiB allocatable. Memory and disk have thrashed before (swap use, probe timeouts during rollouts, MySQL OOM at 250Mi); zram swap was added to cope. Rollouts are `maxSurge: 0` by design (old pod stops first, seconds of downtime). KEDA (workers 1 to 3) is currently suspended, and the stress test falls back to a simulation when the live free-memory gate (`capacity.py`, metrics-server) says no or does not know. Budget table: `docs/DESIGN.md` 9.7.
- **GitOps:** merge to `main`, then `release.yml` pushes ECR `build-N` (the `build-` prefix matters, see traps), then Flux `ImagePolicy` picks the highest, `ImageUpdateAutomation` commits the tag to branch `deploy` (`sync-deploy-branch.yml` also writes it), then Kustomizations apply in order: `flux-system` (root, includes `migrate`) then `app-ready` health checks then `ingest`; KEDA is its own ordered pair (`keda` waits, then `keda-scaling`). Jobs are immutable, so Flux force-recreates them via an annotation.
- **Terraform:** `infra/envs/prod` is planned on same-repo PRs and applied by CI after merge behind the `terraform-prod` environment approval (OIDC, `terraform.yml`). `infra/bootstrap` (state bucket, CI and ops roles) keeps its state in S3 (`bootstrap/terraform.tfstate`, out of CI's reach) and is applied by `bootstrap.yml` after owner approval, only if the re-plan's fingerprint matches the reviewed plan. Node operations go through the "Ops · ..." workflows (`ops.yml`, Terraform-managed `glassbox-ops-*` SSM documents). Nobody applies Terraform or touches AWS by hand. Only the plan role is read-only; apply role and policies live in bootstrap.
- **OIDC:** this account has GitHub's immutable subject on. Every trust `sub` must be `repo:hacka-tron@<owner-id>/basel.engineering@<repo-id>:...` via `local.github_oidc_subject_prefix` in `infra/bootstrap/main.tf`, never the plain `repo:owner/repo` form. STS errors say only "Not authorized".
- **The repo is public.** Nothing sensitive may appear in code, docs, status reports or logs (account IDs, IPs, tokens, state, tfvars).

## Hard rules for reviewers

- Review and validate only. Do NOT edit, create or delete tracked files; do NOT commit, push, rebase or touch PRs.
- Do NOT touch live AWS or Kubernetes (no aws/ssm/kubectl against a real cluster, no terraform apply).
- NEVER read, open, cat, grep or copy any `terraform.tfstate` (or `*.tfstate.backup`, `*.tfvars`, plan files). Review Terraform from `.tf` source and `init -backend=false`.
- You MAY run tests, linters, builds, `kubectl kustomize`, docker compose, local dev servers and throwaway scripts outside the repo. Clean up what you start; use the ports you were given and never stop an existing compose stack.
- Do not trust the brief, the PR body or the implementer's report. Read the diff and every file it integrates with. Cite file:line.
- Report in the dispatch's format. Every Critical/Important needs a concrete failure scenario, ideally one you reproduced.

## Known traps by area

Recurring review findings (cross-cutting):
- **Failure and rollback paths are the ones nobody exercised.** Success path tested, then a script/job/cleanup/refund path fails open or leaves half-state (zram script exits under `set -e` before its rollback; a warm-up refund crossing UTC midnight; Stop/disconnect never closing a Bedrock stream). Force the failure and look at the end state.
- **IAM broader than claimed.** "Scoped to X" comments that the policy does not enforce: resource-wide `Describe/Delete` on SSM associations, bucket-wide `s3:List` for CI roles, ARNs that IAM does not check against `Targets`. Read the actual Action/Resource/Condition and the AWS service authorization reference.
- **Runbook step order vs Terraform behavior.** Plan before `init -migrate-state`, applying a PR locally that a later merge reverts, "pull main" steps that undo local-only changes. Walk the steps literally against what Terraform and git would do.
- **Focus, history and gesture paths untested.** Keyboard focus after close/fallback/clipboard failure, focus order vs visual order, multi-touch/drift/pointercancel, conversation history reuse across reloads.
- **Tests that pass while the bug exists.** Sequential calls labelled concurrent, regex tests that parse a line at a time, tests that assert the helper instead of the shipped path, mocked fetch with no real stream. Revert the fix mentally (or actually, in a scratch copy) and see whether the test fails.

Frontend (`frontend/`, rules in `project/MOBILE_DESIGN.md`):
- Global CSS can silently override utilities (unlayered `font: inherit` beat every Tailwind size once). Check computed sizes, not class names.
- React Flow needs fixed node size and explicit handles or nodes/arrows flicker out on each trace update. The 124x42 node size is deliberate.
- Mobile: `h-dvh` not `100vh`; inputs match the 13 px message text, and iOS focus-zoom is prevented by an iOS-only `maximum-scale=1` in `index.html` (never add it for other platforms, since it blocks pinch-zoom there); >= 44 px tap targets; nothing under 11 px; short phones (375x667) can leave 48 px of message area; hit areas of neighbours can overlap by a few px.
- Stream UX: idle watchdog, Stop keeps partial as `stopped`, errors are friendly replies saved as `error` and never sent as history, copy must never blame the visitor (shared IPs). Cooldowns must use elapsed time, not interval ticks.
- Sheet/dialog focus: trap, Escape, restore on every exit path (including activating a different control).

Backend / RAG (`services/glassbox/`):
- **Grounding is keyword-based, not tense-aware.** The planned-design marker mislabels live infra (KEDA, k3s, Terraform, Flux) or leaves unbuilt detail unmarked; check marks at sentence/row granularity, not whole chunks.
- **Never cache refusals.** Abstentions in many wordings ("I have no information...", "sources don't say...") must stay out of the answer cache; a cached refusal replays forever.
- Embedding-model isolation: chunks are tagged with a hashed model id and search filters on it; a partial re-ingest must not mix vectors. Cache keys must include model + prompt version.
- Budgets: refund to the reserving UTC day; never refund an uncertain call (a timeout after the POST was sent); count every generation attempt; the warmer shares a daily cap and must stop on limit errors. Mixed old/new pod versions must not double-spend (counters carried across deploys).
- Disconnect handling: poll fallback must run during continuous output, not only on quiet; cancel must close the Bedrock stream even before headers arrive.
- Alembic: new columns need migrate-before-api ordering (init gate `wait_for_migrations`, bounded per attempt). DB-backed tests skip without MySQL, so run them or say they skipped.

k8s / Flux (`k8s/`):
- `kustomize.toolkit.fluxcd.io/force` is the string enum `enabled`/`disabled`; `"true"` is silently ignored. Jobs, and StatefulSet/PVC specs, are immutable: a spec change does not touch running pods, and a half-initialised PVC (OOM mid-init) needs the PVC deleted too.
- `ImagePolicy` filter must be `^build-(?P<num>\d+)$`; a bare `^\d+$` once matched an all-digit git SHA and shipped a stale image.
- `ImageRepository` needs `spec.provider: aws` for ECR auth. Flux "Ready" and `status` output prove only that the last apply did not error, not that the intended image runs: compare deployed tag/digest to expectation.
- CRD-then-CR installs (KEDA) need ordered Kustomizations with `wait`; a single one fails first install. Check `dependsOn` chains and health checks, and that `ingest` waits for `app-ready`.
- Memory arithmetic: rows must sum to the stated total; worker figures are projections; admission webhooks and other chart components are often left uncapped. RBAC must be least-privilege (nodes `list`, `metrics.k8s.io`; pods only in `app`); a missing health condition must fail closed, not open.
- `kubectl kustomize` renders prove syntax, not that Flux will apply it in order.

Terraform / IAM (`infra/`):
- Immutable OIDC subject (see above); trust conditions on every new role.
- Provider lock files need linux and darwin hashes (`terraform providers lock -platform=linux_amd64 -platform=darwin_arm64 -platform=linux_arm64`), or CI validate fails on Linux runners.
- `aws_instance` AMI is `ignore_changes` on purpose: a new AL2023 release must not force-replace the node (and wipe k3s, MySQL, Redis).
- Plan-role needs read perms for everything `plan` refreshes (public AMI SSM parameter, ECR describe/list-tags); missing ones fail only in CI. Plan comments must not publish plan files.
- Ask of every policy change: who gets this, on which resources, and does the comment match the JSON. No tfstate reads, ever.
- Moving bootstrap to S3 state: backend block, `init -migrate-state` ordering, who can read the state bucket (CI roles must not list the bootstrap prefix).

Workflows (`.github/workflows/`):
- `release.yml` path filter must cover every path the Dockerfile `COPY`s (corpus, docs, infra, k8s, alembic.ini, .dockerignore); the regression test must parse continued `COPY` lines.
- `build-N` is the run number, so any run outranks older builds: manual `workflow_dispatch` releases must stay gated on "SHA is `main`'s current head" (`release-provenance.sh`; an ancestor check would let old commits through). `sync-deploy-branch` races Flux on `deploy`: it must keep re-fetching, re-merging and pushing without force (`sync-deploy-branch.sh`, tested against a bare repo with a racing push).
- Required checks cannot be path-filtered; the default `GITHUB_TOKEN` never triggers workflows on PRs it opens; environment gates (`terraform-prod`) are the apply control, so check plan jobs never get write credentials.
- Run `actionlint`; read `permissions:`, `if:` fork guards, and which secrets each job can see.

## Per-area checklist (run it yourself and paste summary lines)

- **All:** `git diff origin/main...HEAD --stat`; read every touched file plus what it integrates with; `git diff --check`; grep the diff for secrets, account IDs, IPs.
- **Frontend:** `cd frontend && npm ci && npm run lint && npm test && npm run build`; for UI changes, check 375/414/768/1024/1440 px per `MOBILE_DESIGN.md` (no horizontal scroll, tap targets, arrows visible, focus order); exercise Stop, error and reload paths with a mocked or local `/api/ask`.
- **Backend:** `.venv/bin/python -m pytest services/tests -q` (about 134 tests; note skipped DB-backed ones) and `.venv/bin/ruff check services eval`; for ask/cache/budget changes run a real local compose stack and one end-to-end SSE (`curl -N`) plus a concurrent or failure case.
- **k8s:** `kubectl kustomize k8s/base`, `k8s/overlays/prod`, and each flux/ingest/keda overlay; confirm ordering (`dependsOn`, `wait`), memory limits, RBAC rules, annotation values.
- **Terraform:** `terraform fmt -check -recursive`; in each root `terraform init -backend=false && terraform validate`. No plan, no apply, no state.
- **Workflows:** `actionlint`; trace the trigger, filters, permissions and environment for the claimed behavior.
- **Docs-only:** facts match code (numbers, names, paths); stale SNAPSHOT/handoff roles; conflicting instructions between files.
- Clean up: stop what you started, remove `node_modules`/`dist`/`.terraform` you created.

## Where to read more

- Orientation: `project/SNAPSHOT.md`, `project/BACKLOG.md` (`> RESUME HERE`), `project/AGENT_HANDOFF.md` (top entries; the Task 5 history holds the Flux lessons), `project/status/README.md` (system diagram, per-feature reports with prior review findings).
- Design: `docs/DESIGN.md` (4 UX, 5 architecture, 8 SSE contract, 9 k8s and memory budget, 10 Terraform, 12 CI/CD), `docs/DESIGN-002-followups.md` (chat, resilience), `docs/DESIGN-003-ingestion.md` (planned), `docs/architecture/deep-dive.md`.
- Frontend: `project/MOBILE_DESIGN.md`, `frontend/src/index.css` (token pitfalls).
- Backend: `services/glassbox/api/ask.py`, `trace.py`, `limits.py`, `worker/main.py`, `retrieval/search.py`.
- k8s: `k8s/README.md` (release effects, KEDA section), `k8s/overlays/prod/flux/`.
- Terraform/CI: `infra/CI.md`, `infra/bootstrap/README.md`, `.github/workflows/`.
- Process: `project/orchestration/README.md`, `codex-reviewer.md`.

## Machine constraints

- Disk is tight: delete `node_modules`, `dist`, `.terraform` and docker build leftovers you create; do not `npm ci` if the area is untouched.
- Other agents share the machine and ports: use the ports in the dispatch, never stop an already-running compose stack, kill only your own processes.
- At most about 3 concurrent reviews. If a review is blocked on resources, say so in the report instead of guessing.
