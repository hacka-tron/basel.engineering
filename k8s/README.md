# Manual Glassbox bring-up (Phase 4 MVP)

Run these steps from the repository root. The cluster runs on the one EC2 node
provisioned by `infra/`; there is no public Kubernetes API endpoint. Flux and
GitOps automation of this sequence belong to Phase 6.

1. Apply `infra/bootstrap` first. Follow its README, including replacing the
   placeholder S3 backend bucket in `infra/envs/prod/backend.tf`. Then set the
   zone-scoped Cloudflare credentials and apply production infrastructure:

   ```sh
   cd infra/envs/prod
   terraform init
   terraform plan
   terraform apply
   terraform output instance_id
   terraform output elastic_ip
   ```

   The required Terraform variables are `TF_VAR_cloudflare_api_token` and
   `TF_VAR_cloudflare_zone_id`. Review the plan before applying. Return to the
   repository root before continuing.

2. Build and publish the application image for Linux ARM64 to the registry
   named in the Deployment and Job manifests. The image must be readable by
   the node (for example, publish the GHCR package publicly). From a machine
   with Docker and GHCR write access:

   ```sh
   docker buildx build --platform linux/arm64 \
     -t ghcr.io/hacka-tron/basel.engineering:phase4-k8s --push .
   ```

   For later releases, use a new immutable tag and update all four image
   references before applying. Do not rerun an old Job by changing only its
   image: delete the completed Job and apply its manifest again.

3. Start an AWS SSM Session Manager shell on the node using the production
   `instance_id` output. Clone this repository at the same revision used to
   build the image. On the node, `sudo -i` if needed so k3s's kubeconfig is
   available, then from the repository root run:

   ```sh
   bash k8s/bootstrap-secrets.sh
   ```

   The script reads `/glassbox/mysql/password` through the instance role and
   creates or updates `glassbox-mysql` in both namespaces. It also creates a
   stable private `glassbox-app` rate-limit salt in `app` on first run.

4. Apply in dependency order. The base Kustomization lists every resource for
   future overlays, but applying it in one command would start the Jobs and
   Deployments before migrations finish. For this first manual deploy, run:

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

   If a Job fails, inspect `kubectl -n app logs job/migrate` or `job/ingest`.
   To retry, delete that Job and apply its manifest again. Ingestion can take
   time because it calls Bedrock to embed the corpus.

5. Verify `kubectl get pods -A`. From the node, check the Ingress with
   `curl -H 'Host: basel.engineering' http://127.0.0.1/readyz`; expect
   `"ready":true`. Once Cloudflare DNS resolves, check
   `https://basel.engineering/readyz` and load the site in a browser.

Traefik serves HTTP and HTTPS through two Ingress routes to one API Service. The
HTTPS route uses Traefik's default origin certificate; Cloudflare's encryption
mode must accept that certificate (Full). Full (strict) requires a trusted
origin certificate and a corresponding TLS Secret, a separate certificate
setup task. No Kubernetes LoadBalancer is used.

KEDA autoscaling, the demo load flow, cluster-view RBAC, nightly ingestion,
and Flux/GitOps deployment are separate later phases. When KEDA is installed,
the Redis NetworkPolicy must also allow its operator pods.
