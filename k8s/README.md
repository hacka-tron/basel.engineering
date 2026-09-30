# Kubernetes manifests

Glassbox runs on a single k3s node (see `docs/DESIGN.md` §10 for the full
architecture). `k8s/base` holds every workload; `k8s/overlays/prod` layers on
top of it to pin the deployed image tag in one place.

## Normal operation

`.github/workflows/release.yml` builds and pushes a new image on every merge
to `main`, then bumps `k8s/overlays/prod/kustomization.yaml`'s tag. Once Flux
is bootstrapped (status in `project/AGENT_HANDOFF.md`), it applies that
overlay to the live cluster automatically — no manual step needed for a
routine release.

### What a release does to the running site

Flux applies the whole overlay in one pass, so the recreated `migrate` and
`ingest` Jobs and the new `api`/`retrieval-worker` pods all start together.
The Deployments are set up so that is safe on a 2 GiB node:

- **Brief downtime by design.** `api` and `retrieval-worker` use
  `RollingUpdate` with `maxSurge: 0, maxUnavailable: 1`: the old pod is
  stopped *before* the new one starts, so the node never holds two copies
  (the owner chose memory headroom over zero downtime). Expect the site to be
  unavailable for a few seconds per release — the time for the new api pod to
  start and pass `/readyz` — and longer if a migration is running.
- **Migrations gate the new pods.** Both Deployments have a
  `wait-for-migrations` initContainer
  (`python -m services.glassbox.db.wait_for_migrations`) that polls the
  database's Alembic revision (read-only) until it equals the head baked into
  the image, i.e. until this tag's `migrate` Job has finished. It gives up
  after 5 minutes with an `ERROR ... timed out` log line; the pod then shows
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
- **Not yet ordered:** the `ingest` Job still runs alongside the rollout
  rather than after it (a planned follow-up splits it into its own Flux
  Kustomization).

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
   kubectl apply -f k8s/base/ingest-job.yaml
   kubectl -n app wait --for=condition=complete job/ingest --timeout=15m
   ```

   `kubectl kustomize k8s/overlays/prod` renders the exact manifests
   (including the currently pinned image tag) if you'd rather apply that
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
  `retrieval-worker` 1→5 on `retrieval:jobs`' consumer-group lag.
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
(up to 5). Scale it back explicitly (`kubectl -n app scale
deploy/retrieval-worker --replicas=1`) or re-add `replicas: 1` to
`worker-deployment.yaml` in the same change that removes KEDA.

Installing KEDA is a new cluster-level component: merging this to `main`
(and so to `deploy`) makes Flux install it, which needs the owner's explicit
go-ahead. Validated locally with `kubectl kustomize` on `k8s/overlays/prod`,
`keda`, and `keda-scaling`.

Nightly ingestion is still future work (see `project/BACKLOG.md`).
