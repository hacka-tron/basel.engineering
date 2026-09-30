# App CI/CD Pipeline + Flux GitOps

**Goal:** Close the loop on autonomous development: every PR runs the test suite as a merge gate on `main`; every merge to `main` builds a new application image and Flux applies it to the live cluster, with no manual SSM/kubectl driving required for routine releases.

**Source design:** `docs/DESIGN.md` §12 (CI/CD) and §10.4 (Flux bootstrap prerequisites already noted in `user_data`). Flux/GitOps was originally scoped as Phase 6 (`project/BACKLOG.md`); pulled forward by owner decision on 2026-09-29 after weighing it against a direct-CI-push deploy (see conversation: Flux chosen specifically because it structurally prevents the cluster's live config from drifting away from what CI has committed to git — a real risk given how much of this project's deploy history has been manual `kubectl` over SSM).

**Scope boundary:** This plan does not touch AWS infrastructure or Terraform. `feature/terraform-ci` (already implemented, pending review/merge separately) remains the only pipeline that changes AWS resources. This plan covers application code (Python/frontend) test-gating and Kubernetes-level deployment only. Flux never touches Terraform.

**Runtime facts this plan relies on (verified 2026-09-29):**
- Root `Dockerfile` is the real multi-stage production image (Node frontend build + Python runtime, non-root user) — this is what k8s deploys, not `services/Dockerfile` (a separate lightweight dev-only image used only by `docker-compose.yml`).
- Tests: `pyproject.toml` pins `testpaths = ["services/tests"]`; `services/requirements-dev.txt` pins `pytest`, `pytest-asyncio`, `httpx`, `PyYAML`. **`ruff` is not pinned anywhere** — it's been installed ad hoc into `.venv` in past sessions. Task 1 fixes this.
- Integration tests (`test_worker.py` etc.) connect to real MySQL/Redis via `MYSQL_HOST=127.0.0.1`/`REDIS_URL=redis://127.0.0.1:6379/0` env vars and `pytest.skip` if unreachable — they expect schema already migrated (`alembic upgrade head`), not `create_all`. `docker-compose.yml`'s `mysql`/`redis` service definitions (images, credentials, ports) are the exact fixture to mirror in CI.
- `k8s/base/*.yaml` currently hardcodes `image: ghcr.io/hacka-tron/basel.engineering:9c03c06-secfix` in four files (api-deployment, worker-deployment, migrate-job, ingest-job). No `k8s/overlays/` directory exists yet.
- GHCR package `ghcr.io/hacka-tron/basel.engineering` is public; it was previously pushed with a PAT carrying `write:packages`+`delete:packages` (the account's default token lacked them).

## Task 1 — PR test-gate workflow

- New `.github/workflows/ci.yml`, triggered on `pull_request` targeting `main` and `push` to `main`, scoped to paths `services/**`, `frontend/**`, `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`.
- Pin `ruff` in `services/requirements-dev.txt` (it's currently un-pinned/ad hoc — fix before depending on it in CI).
- Job `backend-tests`: GitHub Actions `services:` containers for `mysql:8.0` and `redis/redis-stack-server:7.2.0-v11`, credentials/ports copied exactly from `docker-compose.yml` (root pw `root`, db/user/pw `glassbox`, healthchecks). Steps: checkout → setup Python 3.12 → `pip install -r services/requirements-dev.txt` → `alembic upgrade head` (env vars matching compose) → `ruff check services eval` → `pytest services/tests -q` with `GLASSBOX_PROVIDER=fake` (no real AWS calls).
- Job `frontend-checks`: `actions/setup-node@v4` Node 22 (matches the Dockerfile's `node:22-alpine`) → `npm ci` → `npm run lint` (oxlint) → `npm run build` (`tsc -b && vite build`).
- **Verification:** open a throwaway PR; confirm both jobs run. Break one test and one lint rule temporarily to confirm the checks actually fail (not just "ran"), then revert.

## Task 2 — Enforce the gate

- Configure GitHub branch protection on `main` (via `gh api repos/hacka-tron/basel.engineering/branches/main/protection` or the Settings UI) requiring the `backend-tests` and `frontend-checks` status checks before merge.
- **Verification:** read back the protection rule via `gh api`; confirm a PR with a failing check shows a blocked-merge state in the GitHub UI.

## Task 3 — Restructure k8s manifests for image-tag bumps

- Add `k8s/overlays/prod/kustomization.yaml` (`resources: [../../base]`) with a kustomize `images:` transformer pinning the deployed tag in one place; change the four hardcoded `image:` lines in `k8s/base/*.yaml` to a stable placeholder name the transformer rewrites (standard kustomize pattern — single source of truth for "what tag is deployed").
- Update `k8s/README.md`'s manual bring-up steps to apply against `-k k8s/overlays/prod`, preserving the documented dependency order (namespaces → data → wait for `migrate` Job → app workloads → `ingest` Job).
- **Verification:** `kubectl kustomize k8s/overlays/prod` renders locally without error and produces the same four workloads with the correct tag substituted. No live cluster touched in this task.

## Task 4 — Build, push, and bump-manifest release workflow

- New `.github/workflows/release.yml`, triggered on `push` to `main`, same paths as Task 1 plus `k8s/base/**`.
- Job `build-and-push`: `docker/setup-buildx-action`, `docker/login-action` to `ghcr.io` using the built-in `GITHUB_TOKEN` with `packages: write` permission (verify it can push to the existing package without a PAT on the first real run; fall back to a documented PAT secret only if rejected). `docker/build-push-action`, `platforms: linux/arm64`, build context `.` with the root `Dockerfile`, tags `:<short-sha>` and `:latest`.
- Job `bump-manifest` (needs `build-and-push`): checkout with `contents: write` permission, `kustomize edit set image` inside `k8s/overlays/prod` to the new `<short-sha>`, commit (`deploy: bump image to <short-sha> [skip ci]`) and push directly to `main`. Path-scope both workflows so this bot commit doesn't re-trigger `ci.yml`'s full suite or loop `release.yml`.
- **Verification:** merge a trivial change through the pipeline; confirm the new tag appears in GHCR and the bump commit lands on `main` touching only the kustomize image line.

## Task 5 — Bootstrap Flux on the live cluster

**Requires explicit owner go-ahead immediately before running** — this is the first time automated tooling (rather than a human/agent driving `kubectl` by hand over SSM) writes to the live cluster's config.

- Flux CLI is already installed on the node (per `project/AGENT_HANDOFF.md`). Via SSM: `flux bootstrap github --owner=hacka-tron --repository=basel.engineering --branch=main --path=k8s/overlays/prod --personal`, using a fine-grained PAT scoped to only this repo, read from an SSM SecureString rather than typed into shell history.
- Confirm Flux's `GitRepository`/`Kustomization` reconcile cleanly against the already-live manifests with no unintended diff or pod restart before any new image lands.
- Update `k8s/README.md`, `project/SNAPSHOT.md`, and `project/BACKLOG.md` to mark GitOps live and document the recovery path if Flux ever misapplies something (`flux suspend kustomization` then manual `kubectl`, then `flux resume`).
- **Verification:** run one real end-to-end release through Tasks 1–4's pipeline and confirm Flux picks it up within its poll interval with zero manual kubectl/SSM steps, then confirm `https://basel.engineering/readyz`.

## Task 6 (deferred, scope on request) — Redis-backed incident kill-switches

Discussed alongside this plan but out of scope for closing the CI/CD loop itself: a couple of Redis-backed runtime flags (e.g. `disable_llm` forcing retrieval-only mode) for instant incident mitigation without racing a hotfix through the pipeline. Revisit separately.

## Handoff discipline

- Work in a dedicated worktree/branch (`feature/app-cicd`), one commit per task, per `project/CLAUDE.md` commit discipline and `project/CODEX.md`'s worktree/review-gate policy.
- Update `project/SNAPSHOT.md`/`BACKLOG.md` resume pointer at the end of each task, not just at the end of the plan.
