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
- **Answer-cache warm-up.** After ingesting, the ingest Job runs
  `python -m services.glassbox.warm`, which asks the suggested questions
  through the `api` Service so their answers are cached again (a changed
  corpus invalidates them); a warm-up failure is logged and ignored. The
  `warm-answers` CronJob (`base/warm-cronjob.yaml`, every 2h, 48Mi limit)
  does the same between deploys. All runs share a daily cap of
  `GLASSBOX_WARM_DAILY_LLM_CAP` (10, in the ConfigMap) LLM calls, counted in
  Redis as `warm:budget:{date}`, so the CronJob pod may reach Redis (not
  MySQL). Both pods carry
  `glassbox/answer-warmer: "true"`, which the `api-from-answer-warmer`
  NetworkPolicy admits to the api on port 8000. Check a run with
  `kubectl -n app logs job/ingest | tail` or the latest `warm-answers-*` Job.

## Incidents: push-button runbooks

Use the **Actions → "Ops · ..."** workflows (one per runbook, wrappers around `.github/workflows/ops.yml`) instead of SSM
sessions and hand-typed `kubectl`/`flux`. Every action is described in
`infra/CI.md` under "Runbooks". `diagnose` needs no approval. Everything
else waits for the owner's approval and prints a diagnose before and after.

| Symptom | Run (workflow, then inputs) |
|---|---|
| Anything looks wrong | **Ops · Diagnose** first. Read the memory PSI, swap in/out (`si`/`so`), node conditions, unready pods, warning events, and the k3s error and Slow SQL counts. |
| Site returns 521/522 or times out, and `diagnose` fails, times out or shows the apiserver not answering | **Ops · Reboot node**. If the after-diagnose shows no `/dev/zram0`, run **Ops · Apply zram**. |
| Node responsive but thrashing (memory PSI `full` stays high, heavy `si`/`so`, many Slow SQL lines), or KEDA's Helm release keeps retrying | **Ops · Flux suspend or resume** (suspend) `helmrelease-keda`, then `keda`, then **Ops · KEDA on or off** `off (0)`. Optionally **Ops · Warm-up CronJob suspend or resume** (suspend). |
| …incident over | In reverse: **KEDA on or off** `on (1)`, **Flux suspend or resume** (resume) `helmrelease-keda` then `keda`, **Warm-up CronJob** (resume). |
| Answers come back as sources only (`retrieval_only`, "I can't write a full answer right now") | **Ops · Diagnose**, section "LLM budget": fewer than 4 units left means the daily budget is spent (it resets at 00:00 UTC); "kill switch ... ON" means the LLM was switched off. The hourly query counts and the busiest rate-limit buckets show where the budget went. |
| `api` Running but not serving (stuck streams, readiness flapping) | **Ops · Restart deployment** `api`. This causes a few seconds of downtime. |
| A merged fix should deploy now, or a release is stuck behind Flux's interval | **Ops · Flux reconcile**. |
| Need to stop all deploys while investigating | **Ops · Flux suspend or resume** `flux-system` (suspend), and later resume. |
| Email from `glassbox-alerts`: `glassbox-node-reboot` or `glassbox-node-recover` in ALARM | AWS is already acting (reboot, or recover onto new hardware; same instance, IP and disk). Wait for the OK email, then **Ops · Diagnose**. If no OK comes within about 15 minutes, **Ops · Reboot node**. |
| GitHub email "Uptime probe" failed (`/readyz` not ready from outside) | **Ops · Diagnose**, then the matching row above. One failed run followed by green ones is usually a blip. |
| Disk state is bad and a day's loss is acceptable (broken migration or upgrade, corrupted MySQL/k3s data, lost data) | **Ops · List snapshots**, pick a snapshot ID from before the problem (or use `latest`), then **Ops · Restore from snapshot** with `old_volume` `keep`. The node reboots onto the restored disk (same instance and IP, a few minutes down); everything written since that snapshot, including questions asked, is lost. Check the after-diagnose. |

Anything not on this list still needs an SSM session (see below). If it
keeps coming up, add it as a new `glassbox-ops-*` document in
`infra/modules/ops` rather than repeating it by hand.

## Manual apply / disaster recovery

If the instance still exists and a day-old disk is acceptable, use
**Ops · Restore from snapshot** instead (see the table above): it puts back
k3s, MySQL and Redis in one step.

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

## Node memory and swap

The node has 1.84 GiB allocatable and runs over physical memory at times. So
swap goes to compressed RAM first (`/dev/zram0`, priority 100), and the
EBS-backed `/swapfile` (priority -2) is only overflow. This keeps swap traffic
off the disk that k3s's SQLite datastore and MySQL share. It is host config,
not a Kubernetes object: Terraform's SSM association `glassbox-zram-swap` sets
it up (`infra/modules/compute/zram.tf`, `zram-swap.sh`). The kubelet runs
with `fail-swap-on=false`, and pods' `memory.swap.max` is `max`, so pod memory
can swap too. To check it (as root via SSM): `swapon --show`, `zramctl`,
`cat /proc/pressure/memory /proc/pressure/io`. Rationale, tuning, baseline
numbers and rollback are in `docs/DESIGN.md` §9.7.

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
