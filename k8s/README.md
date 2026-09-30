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

`feature/stress-test-keda` adds the pieces for the "Stress test" demo:

- `k8s/base/rbac-api.yaml` — a scoped `api` ServiceAccount + Role +
  RoleBinding (get/list/watch on `pods`, `app` namespace only), wired into
  `api-deployment.yaml` via `serviceAccountName: api`. Backs
  `GET /api/cluster/stream`.
- `k8s/overlays/prod/keda/` — KEDA itself, installed via a Flux
  `HelmRepository`/`HelmRelease` (not Terraform's helm provider — this repo
  has no Terraform-managed Kubernetes resources; Flux already owns every
  other piece of live cluster config, so a second, Terraform-driven install
  path would risk exactly the drift Flux was chosen to prevent). Verify the
  chart version pin in `helm-release.yaml` against the current latest KEDA
  release before applying — it was written without live access to the chart
  index.
- `k8s/overlays/prod/scaledobject-retrieval-worker.yaml` — the `ScaledObject`
  scaling `retrieval-worker` 1→5 on `retrieval:jobs`' consumer-group lag.
  `k8s/base/worker-deployment.yaml` no longer pins a fixed `replicas` so KEDA's
  HPA (not a stray `kubectl apply`/Flux reconcile) owns that field.
- `k8s/base/networkpolicy-data.yaml`'s `redis-from-app` policy now also
  allows ingress from the `keda` namespace, since KEDA's redis-streams
  scaler polls Redis directly from its own operator pod, not through
  `retrieval-worker`.

**None of this has been applied to the live cluster.** Installing KEDA is a
new cluster-level component and needs the owner's explicit go-ahead
immediately before running, per this project's standing rule for live
cluster changes (see `project/AGENT_HANDOFF.md`). Rendered and validated
locally only: `kubectl kustomize k8s/base` / `k8s/overlays/prod`, every
resulting document parses as valid YAML, and `terraform fmt`/`validate` still
pass across `infra/` (unchanged by this branch — KEDA is Flux/kustomize, not
Terraform).

Nightly ingestion is still future work (see `project/BACKLOG.md`).
