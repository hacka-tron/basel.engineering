# Kubernetes manifests

Glassbox runs on a single k3s node (see `docs/DESIGN.md` §10 for the full
architecture). `k8s/base` holds every long-running workload plus the `migrate`
Job; `k8s/overlays/prod` layers on top of it to pin the deployed image tag and
adds Flux's own objects. The `ingest` Job lives in `k8s/overlays/prod/ingest`
and is applied by a separate, dependent Flux Kustomization (see below).

## Normal operation

`.github/workflows/release.yml` builds and pushes a new `build-N` image on every
merge to `main`. Flux's ImageUpdateAutomation (not the workflow) then commits
the new tag into the `images:` blocks under `k8s/overlays/prod/` on the
`deploy` branch. Once Flux is bootstrapped (status in `project/AGENT_HANDOFF.md`), it applies that
overlay to the live cluster automatically — no manual step needed for a
routine release.

### What a release does to the running site

Flux's root `flux-system` Kustomization applies the overlay in one pass, so
the recreated `migrate` Job and the new `api`/`retrieval-worker` pods start
together; the `ingest` Job follows only once the new pods are Ready. The
Deployments are set up so that is safe on a 2 GiB node:

- **Brief downtime by design.** `api` and `retrieval-worker` use
  `RollingUpdate` with `maxSurge: 0, maxUnavailable: 1`: a rollout never adds
  an extra pod, so the old pod is stopped *before* its replacement starts
  (the owner chose memory headroom over zero downtime). For the single api
  replica that means no old/new overlap at all; when KEDA has scaled the
  worker above one replica, old- and new-image workers coexist while
  replicas are replaced one at a time, but never more pods than replicas. Expect the site to be
  unavailable for a few seconds per release — the time for the new api pod to
  start and pass `/readyz` — and longer if a migration is running.
- **Migrations gate the new pods.** Both Deployments have a
  `wait-for-migrations` initContainer
  (`python -m services.glassbox.db.wait_for_migrations`) that polls the
  database's Alembic revision (read-only) until it equals the head baked into
  the image, i.e. until this tag's `migrate` Job has finished. It gives up
  after 5 minutes with an `ERROR ... timed out` log line (each read is capped
  at 5s with its own connection and socket timeouts, so a stalled MySQL can't
  stretch the wait past that bound); the pod then shows
  `Init:Error`/`Init:CrashLoopBackOff` and the kubelet retries it. Check
  `kubectl -n app logs deploy/api -c wait-for-migrations` and
  `kubectl -n app logs job/migrate`. Rolling *back* to an image older than the
  database's schema also blocks here (the revisions no longer match), which is
  deliberate: it refuses to run code against a schema it doesn't know.
- **Graceful shutdown.** Uvicorn runs with `--timeout-graceful-shutdown 25`
  (Dockerfile `CMD`) inside the api's `terminationGracePeriodSeconds: 30`, so
  in-flight requests and SSE streams get up to 25s to finish before SIGKILL.
- **Probes tolerate swap pressure.** Readiness and liveness use
  `timeoutSeconds: 5`; liveness needs 6 consecutive failures (2 minutes at a
  20s period) before a restart. A `startupProbe` on `/healthz` (every 5s, up
  to 30 failures = 150s) holds off liveness until the process first answers,
  so a slow boot isn't killed mid-start.
- **Ingest runs after the rollout.** `flux/kustomization-ingest.yaml`
  defines two child Kustomizations. `app-ready` (empty path
  `k8s/overlays/prod/app-ready`) dependsOn the root `flux-system`, so it only
  runs once the root has applied this Git revision, then health-checks the
  `api` and `retrieval-worker` Deployments (timeout 10m). `ingest` (path
  `k8s/overlays/prod/ingest`) dependsOn `app-ready`, then applies the Job
  with `wait: true` (timeout 15m). The Job keeps
  `kustomize.toolkit.fluxcd.io/force: enabled`, and `ingest/kustomization.yaml`
  carries its own `$imagepolicy` setter, so Flux bumps its tag in the same
  commit and recreates it per release. Ingestion is content-hash incremental,
  so a routine run only embeds changed docs. To follow a release:
  `flux get kustomizations` (expect `flux-system` → `app-ready` → `ingest`
  Ready at the same revision), then `kubectl -n app logs job/ingest`. If a
  rollout never becomes Ready, `app-ready` times out and ingest simply does
  not run for that revision. This relies on the root `flux-system`
  Kustomization not having `wait: true` (the `flux bootstrap` default) —
  otherwise it would wait on `ingest`, which waits on it.

## Manual apply / disaster recovery

The node has no SSH and no public Kubernetes API — access is AWS SSM Session
Manager only. Use this sequence to rebuild the cluster from scratch or
recover after a manual intervention:

1. Start an SSM session on the production instance (`terraform output
   instance_id` from `infra/envs/prod`). Clone this repo at the revision you
   want; `sudo -i` if needed so k3s's kubeconfig is available.
2. `bash k8s/bootstrap-secrets.sh` — reads `/glassbox/mysql/password` via the
   instance role and creates/updates the `glassbox-mysql` Secret in both
   namespaces, plus a stable `glassbox-app` rate-limit salt on first run.
3. Apply in dependency order. The base Kustomization lists every resource,
   but applying it all in one command starts Jobs/Deployments before
   migrations finish:

   ```sh
   kubectl apply -f k8s/base/namespace-app.yaml -f k8s/base/namespace-data.yaml
   kubectl apply -f k8s/base/mysql-service.yaml -f k8s/base/redis-service.yaml
   kubectl apply -f k8s/base/mysql-statefulset.yaml -f k8s/base/redis-statefulset.yaml
   kubectl apply -f k8s/base/networkpolicy-data.yaml -f k8s/base/networkpolicy-app.yaml
   kubectl -n data rollout status statefulset/mysql --timeout=5m
   kubectl -n data rollout status statefulset/redis --timeout=5m
   kubectl apply -f k8s/base/configmap-app.yaml
   kubectl apply -f k8s/base/migrate-job.yaml
   kubectl -n app wait --for=condition=complete job/migrate --timeout=5m
   kubectl apply -f k8s/base/api-service.yaml -f k8s/base/api-deployment.yaml -f k8s/base/worker-deployment.yaml -f k8s/base/api-ingress.yaml
   kubectl -n app rollout status deployment/api --timeout=5m
   kubectl -n app rollout status deployment/retrieval-worker --timeout=5m
   kubectl kustomize k8s/overlays/prod/ingest | kubectl apply -f -
   kubectl -n app wait --for=condition=complete job/ingest --timeout=15m
   ```

   `kubectl kustomize k8s/overlays/prod` renders the exact manifests
   (including the currently pinned image tag, but not the `ingest` Job —
   render that with `kubectl kustomize k8s/overlays/prod/ingest`) if you'd rather apply that
   directly once past initial bring-up. If a Job fails, check `kubectl -n
   app logs job/migrate` or `job/ingest`, then delete and re-apply it — Job
   pod templates are immutable and can't be patched in place.
4. Verify: `kubectl get pods -A`, then from the node `curl -H 'Host:
   basel.engineering' http://127.0.0.1/readyz` should return `"ready":true`.
   Publicly, check `https://basel.engineering/readyz`.

## Networking

Traefik serves HTTP and HTTPS through two Ingress routes to one API Service;
no Kubernetes LoadBalancer is used. The HTTPS route uses Traefik's default
self-signed certificate, so Cloudflare's SSL/TLS mode must stay on **Full**
(not "Full (strict)", which would need a trusted origin certificate and a
TLS Secret this cluster doesn't have yet).

## Stress test / KEDA autoscaling (DESIGN.md §9.3/§9.4)

The "Stress test" demo is made of:

- `k8s/base/rbac-api.yaml` — the `api` ServiceAccount (wired into
  `api-deployment.yaml` via `serviceAccountName: api`) with a namespaced
  Role for get/list/watch on `pods` in `app` (backs `GET /api/cluster/stream`),
  plus a ClusterRole that can only `list` `nodes` (backs
  `GET /api/demo/capacity`), plus `list` on `nodes` in `metrics.k8s.io` for
  live node usage from metrics-server. If either read fails, the check fails
  closed and the site runs the visual-only demo.
- `k8s/overlays/prod/keda/` — KEDA itself, installed via a Flux
  `HelmRepository`/`HelmRelease` (not Terraform's helm provider — Flux owns
  all live cluster config, so a second install path would risk drift).
- `k8s/overlays/prod/keda-scaling/` — the `ScaledObject` scaling
  `retrieval-worker` 1→3 on `retrieval:jobs`' consumer-group lag.
- **Install order:** the root overlay doesn't apply those two directories
  directly. `k8s/overlays/prod/flux/kustomization-keda.yaml` defines two Flux
  Kustomizations: `keda` (`wait: true`, so it's Ready only once the
  HelmRelease has installed the chart and registered the `keda.sh` CRDs) and
  `keda-scaling` (`dependsOn: keda`). A first install therefore never
  dry-runs the `ScaledObject` before its CRD exists, and a KEDA failure can't
  block the app workloads.
- `k8s/base/worker-deployment.yaml` doesn't pin `replicas`, so KEDA's HPA
  owns that field. A fresh Deployment defaults to 1.
- `k8s/base/networkpolicy-data.yaml` admits the `keda` namespace to Redis,
  since the redis-streams scaler polls it from KEDA's operator pod.

**Removing KEDA later:** deleting the `ScaledObject`/HPA does not reset the
worker count — the Deployment keeps whatever replica count KEDA last set
(up to 3). Scale it back explicitly (`kubectl -n app scale
deploy/retrieval-worker --replicas=1`) or re-add `replicas: 1` to
`worker-deployment.yaml` in the same change that removes KEDA.

Installing KEDA is a new cluster-level component: merging this to `main`
(and so to `deploy`) makes Flux install it, which needs the owner's explicit
go-ahead. Validated locally with `kubectl kustomize` on `k8s/overlays/prod`,
`keda`, and `keda-scaling`.

Nightly ingestion is still future work (see `project/BACKLOG.md`).
