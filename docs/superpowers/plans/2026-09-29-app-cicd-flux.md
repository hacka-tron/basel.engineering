# App CI/CD Pipeline + Flux GitOps

**Goal:** Close the loop on autonomous development: every PR runs the test suite as a merge gate on `main`; every merge to `main` builds a new application image and Flux applies it to the live cluster, with no manual SSM/kubectl driving required for routine releases.

**Source design:** `docs/DESIGN.md` §12 (CI/CD) and §10.4 (Flux bootstrap prerequisites already noted in `user_data`). Flux/GitOps was originally scoped as Phase 6 (`project/BACKLOG.md`); pulled forward by owner decision on 2026-09-29 after weighing it against a direct-CI-push deploy (Flux chosen specifically because it structurally prevents the cluster's live config from drifting away from git — a real risk given how much of this project's deploy history has been manual `kubectl` over SSM).

**Scope boundary (revised):** originally scoped to exclude Terraform/AWS entirely (that was `feature/terraform-ci`'s territory). Revised 2026-09-30 to include one new piece of AWS infrastructure — an ECR repository and its OIDC push role — after the image-registry design below changed. Everything else about AWS (the EC2 node, Cloudflare, the Terraform CI pipeline itself) remains `feature/terraform-ci`'s separate, independent scope.

## Design history: why this isn't the original plan (read before Task 4)

The first version of this plan had `release.yml` push to GHCR using the default `GITHUB_TOKEN`, then have a second job (`bump-manifest`) commit the new image tag into `k8s/overlays/prod/kustomization.yaml` and push that directly to `main` for Flux to pick up. Both halves of that design failed for real, separate reasons, in this order:

1. **GHCR push denied.** `ghcr.io/hacka-tron/basel.engineering` was originally created by a human pushing manually with a personal token, not by a workflow — its Actions-token linkage never worked reliably, even after explicitly granting the repo "Write" access in the package's own settings. Confirmed twice by real failed runs (`permission_denied: write_package`), including once after the fix that was supposed to resolve it.
2. **Direct push to `main` rejected outright.** `main`'s branch protection requires `backend-tests`/`frontend-checks` to pass — and GitHub enforces this *more* strictly for a raw `git push` than for a PR merge: a brand-new commit can never already carry a passing check run for its own not-yet-pushed SHA, so a direct push is rejected unconditionally. Restructuring `bump-manifest` to open a real PR instead hit a second wall: GitHub does not trigger other workflows for a PR opened using the workflow's own default `GITHUB_TOKEN` (anti-loop protection), so that PR's checks would never run and it would never become mergeable.

Fixing both would have meant: a PAT (or GitHub App) for GHCR, a *second* PAT for the deploy-PR to actually trigger checks, plus re-running the full test suite against a commit that only bumps a tag number (the app code in it was already tested before the image was ever built). Workable, but a lot of credential-juggling for what should be simple.

**The actual fix changes the architecture, not just the credentials:**
- **ECR instead of GHCR**, pushed to via OIDC — the same zero-stored-secret pattern `feature/terraform-ci` already uses for AWS. No token to create, rotate, or leak, and it sidesteps the GHCR linkage problem entirely rather than working around it.
- **Flux Image Update Automation instead of a CI-driven manifest bump.** Flux gets a component that watches the ECR repository directly, notices new image tags on its own polling cycle, and updates + commits `k8s/overlays/prod/kustomization.yaml` **itself**, using Flux's own git credential — not GitHub Actions' token. `release.yml`'s job shrinks to exactly one thing: build the image, push it to ECR. There is no `bump-manifest` job anymore, no deploy-PR, no second PAT, and no redundant re-test of already-tested code.

This is a better design on its own merits, not just a workaround: it's fewer credentials, fewer moving parts in `release.yml`, and it matches how registry-driven GitOps is normally done (Flux's own docs recommend this exact pattern over CI writing tags into git).

**Runtime facts this plan relies on (verified across 2026-09-29 and 2026-09-30):**
- Root `Dockerfile` is the real multi-stage production image (Node frontend build + Python runtime, non-root user) — this is what k8s deploys, not `services/Dockerfile` (a separate lightweight dev-only image used only by `docker-compose.yml`).
- Tests: `pyproject.toml` pins `testpaths = ["services/tests"]`; `services/requirements-dev.txt` pins `pytest`, `pytest-asyncio`, `httpx`, `PyYAML`, `ruff` (ruff was previously un-pinned/ad hoc — fixed in Task 1).
- Integration tests (`test_worker.py` etc.) connect to real MySQL/Redis via `MYSQL_HOST=127.0.0.1`/`REDIS_URL=redis://127.0.0.1:6379/0` env vars and `pytest.skip` if unreachable — they expect schema already migrated (`alembic upgrade head`), not `create_all`.
- `k8s/base/*.yaml` currently hardcodes `image: ghcr.io/hacka-tron/basel.engineering:...` in four files (api-deployment, worker-deployment, migrate-job, ingest-job); `k8s/overlays/prod/kustomization.yaml` pins the tag via kustomize's `images:` transformer, matched by repository name across all four. Both need their image *name* (not just tag) updated once the ECR repository URL exists.
- `main`'s branch protection: `required_status_checks` on `backend-tests`+`frontend-checks`, `enforce_admins: true`, no PR-review requirement. `ci.yml` has no path filters (removed after discovering path-filtered-out commits never get a required check run at all, with no automatic leniency for direct pushes and none reliably observed for PRs either).
- `infra/bootstrap` (local Terraform state, applied manually, no remote backend) already defines the GitHub OIDC provider and the `glassbox-ci` role — the new ECR-push role in Task 4a follows that same file's existing pattern.

## Task 1 — PR test-gate workflow ✅ done

`.github/workflows/ci.yml`: `backend-tests` (real MySQL/Redis service containers, `alembic upgrade head`, `ruff check`, `pytest`) and `frontend-checks` (`npm run lint`, `npm run build`), on every PR to `main` and push to `main`, no path filters. Verified live: pass → intentional failure → pass, on real GitHub Actions.

## Task 2 — Enforce the gate ✅ done

Branch protection configured via `gh api` on `main`: both jobs required, `enforce_admins: true`.

## Task 3 — Restructure k8s manifests for image-tag bumps ✅ done, image name needs updating

`k8s/overlays/prod/kustomization.yaml` added. Once the ECR repository exists (Task 4a), update its `images[0].name` (and the four `image:` lines in `k8s/base/*.yaml`) from `ghcr.io/hacka-tron/basel.engineering` to the real ECR repository URI.

## Task 4a — ECR repository and OIDC push role (new)

- Add an `aws_ecr_repository` resource for the application image to `infra/envs/prod` (it's an application resource, alongside the EC2 instance — not bootstrap-level).
- Add a new IAM role to `infra/bootstrap/main.tf`, following the exact pattern already used for `glassbox-ci`/`glassbox-ci-plan`: OIDC trust scoped to a dedicated GitHub environment (e.g. `release`) for `hacka-tron/basel.engineering`, permissions limited to `ecr:GetAuthorizationToken` (resource `*`, ECR requires this) plus `ecr:BatchCheckLayerAvailability`/`PutImage`/`InitiateLayerUpload`/`UploadLayerPart`/`CompleteLayerUpload`/`BatchGetImage` scoped to just this one repository's ARN.
- **Verification:** `terraform plan` in both roots shows only additions (the ECR repo, the new role + policy) — reviewed with the owner before `terraform apply`, since `infra/bootstrap` has no remote backend and is applied manually per its own README, and this is real AWS infrastructure.

## Task 4b — Build-and-push release workflow (revised, no bump-manifest)

- `.github/workflows/release.yml`, triggered on `push` to `main`. One job: `build-and-push`.
- `aws-actions/configure-aws-credentials` (OIDC, `role-to-assume` from Task 4a, `environment: release`) → `aws ecr get-login-password | docker login` → `docker/build-push-action` on the native ARM64 runner (`ubuntu-24.04-arm`, no QEMU — already fixed after the first real run took 20+ minutes emulated), tags `:<short-sha>` and `:latest`, pushed to the ECR repository.
- No second job. No PAT. No deploy-PR. Flux (Task 5) takes it from here.
- **Verification:** merge a trivial change; confirm the new tag appears in ECR via `aws ecr describe-images`.

## Task 5 — Bootstrap Flux with Image Update Automation

**Requires explicit owner go-ahead immediately before running** — first automated write to the live cluster's config, and first time Flux gets git *write* (not just read) access.

**Blocked at preflight (2026-09-30): do not run the bootstrap command below yet.** GitHub's live branch-protection API confirms `main` requires `backend-tests` and `frontend-checks` with `enforce_admins: true`, so Flux's direct bootstrap commit and later `ImageUpdateAutomation` push to `main` would be rejected for the same reason the earlier CI manifest-bump push failed. A fine-grained PAT does not exempt that push. The new ECR repository is private, while `glassbox-instance` has no ECR read actions. A read-only SSM check of the live node also confirmed that `/var/lib/rancher/credentialprovider/bin`, `/var/lib/rancher/credentialprovider/config.yaml`, and `/etc/rancher/k3s/registries.yaml` do not exist (command `c73a63a1-1c29-4516-a8d6-efebacef0713`). A new pod would be unable to pull its image. Revise and review the Git write path and ECR image-pull authentication before requesting the owner's bootstrap approval. Keep the live cluster on its current manually deployed image until both are addressed.

- Flux CLI is already installed on the node. Via SSM: `flux bootstrap github --owner=hacka-tron --repository=basel.engineering --branch=main --path=k8s/overlays/prod --personal`, using a fine-grained PAT scoped to only this repo (Contents: read/write — Flux needs to commit, not just pull), read from an SSM SecureString rather than typed into shell history.
- Install Flux's image automation components (`flux install --components-extra=image-reflector-controller,image-automation-controller`, or included by default depending on bootstrap flags — verify which at bootstrap time).
- Configure `ImageRepository` (scans the ECR repo from Task 4a — needs the node's existing IAM instance role, or a separate credential, to read from ECR; check what access the node already has), `ImagePolicy` (tags are short git SHAs, not semver — use a policy that tracks image *creation timestamp* from registry metadata, not lexical/numeric tag ordering, since short SHAs have no meaningful sort order as strings), and `ImageUpdateAutomation` (writes the resolved tag into `k8s/overlays/prod/kustomization.yaml` and commits+pushes using Flux's own credential).
- Confirm Flux's `GitRepository`/`Kustomization` reconcile cleanly against the already-live manifests with no unintended diff or pod restart before any new image lands.
- Update `k8s/README.md`, `project/SNAPSHOT.md`, and `project/BACKLOG.md` to mark GitOps live and document the recovery path if Flux ever misapplies something (`flux suspend kustomization` then manual `kubectl`, then `flux resume`).
- **Verification:** merge a trivial app change through Tasks 1–4b, confirm Flux notices the new ECR tag within its poll interval, commits the bump itself (inspect that commit's author), applies it, with zero manual kubectl/SSM/GitHub Actions steps beyond the original merge. Then confirm `https://basel.engineering/readyz`.

## Task 6 (deferred, scope on request) — Redis-backed incident kill-switches

Already implemented by Codex on `feature/incident-flags` (a Redis-backed `disable_llm` flag forcing retrieval-only mode, reviewed and verified locally — 115 tests passing). Not pushed/merged yet. Independent of the rest of this plan.

## Handoff discipline

- This work continues in `feature/ecr-flux-images`, one commit per task, per `project/CLAUDE.md` commit discipline.
- Update `project/SNAPSHOT.md`/`BACKLOG.md` resume pointer at the end of each task, not just at the end of the plan.
