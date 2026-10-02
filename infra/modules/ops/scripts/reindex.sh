# glassbox-ops-reindex: run `python -m services.glassbox.ingest.run --reindex`
# once, as a one-off Job in the app namespace, and wait for it. No arguments.
#
# --reindex rewrites every Redis chunk:{id} key of the configured embedding
# model from MySQL (stored vectors, no file scan, no embedding call) and
# removes orphan keys past the 30% fraction guard (never the zero-row guard).
# Every chunk key it writes also drops that id's chunktxt:{id} text cache.
# Use it after a MySQL restore, or when the ingest log shows
# REDIS RECONCILE REFUSED for a deletion that is really wanted.
#
# The Job runs the image the api Deployment runs right now (the current
# release), with the ingest Job's config (k8s/overlays/prod/ingest/
# ingest-job.yaml: ConfigMap, MySQL password reference, pull secret,
# app=ingest label for the data NetworkPolicies). tests/reindex-test.sh
# checks that those names still match. It refuses while a release is
# rolling out or an ingest/reindex Job is running; the Redis lock
# ingest:lock is the second guard (a locked-out --reindex exits 75).

# reindex_manifest <job-name> <image>: the Job, on stdout.
reindex_manifest() {
  local name=$1 image=$2
  cat <<MANIFEST
apiVersion: batch/v1
kind: Job
metadata:
  name: $name
  namespace: app
  labels:
    app: ingest
    glassbox/ops: reindex
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 900
  ttlSecondsAfterFinished: 86400
  template:
    metadata:
      labels:
        app: ingest
        glassbox/ops: reindex
    spec:
      restartPolicy: Never
      imagePullSecrets:
        - name: regcred
      containers:
        - name: reindex
          image: $image
          command: ["python", "-m", "services.glassbox.ingest.run", "--reindex"]
          envFrom:
            - configMapRef:
                name: glassbox-config
          env:
            - name: MYSQL_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: glassbox-mysql
                  key: mysql-password
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              memory: 384Mi
MANIFEST
}

main() {
  exec </dev/null 2>&1
  set -euo pipefail
  local jobs line job_name active rollout generation observed want updated ready
  local image name deadline state

  # 1. Nothing else may be writing the index: no running ingest Job (a
  # release) and no earlier reindex still going.
  jobs=$(kc -n app get jobs -o jsonpath='{range .items[*]}{.metadata.name} {.status.active}{"\n"}{end}')
  while read -r job_name active; do
    [ -n "$job_name" ] || continue
    case "$job_name" in
      ingest | ops-reindex-*)
        if [ -n "$active" ] && [ "$active" != 0 ]; then
          die "job/$job_name is still running; wait for it (the Diagnose runbook lists Jobs) and run this again"
        fi
        ;;
    esac
  done <<<"$jobs"

  # 2. The api rollout is complete, so its image is the current release.
  # "|"-separated: a missing status field (0 ready replicas) stays empty
  # instead of shifting the others.
  rollout=$(kc -n app get deployment api -o jsonpath='{.metadata.generation}|{.status.observedGeneration}|{.spec.replicas}|{.status.updatedReplicas}|{.status.readyReplicas}')
  IFS='|' read -r generation observed want updated ready <<<"$rollout"
  if [ "${generation:-x}" != "${observed:-y}" ] || [ "${updated:-0}" != "${want:-1}" ] || [ "${ready:-0}" != "${want:-1}" ]; then
    die "deployment/api is mid-rollout (generation/observed/replicas/updated/ready: $rollout); run this once the release has finished"
  fi
  image=$(kc -n app get deployment api -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].image}')
  [[ $image =~ ^[0-9]+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/glassbox:[A-Za-z0-9._-]+$ ]] ||
    die "unexpected api image '$(printf '%s' "$image" | redact 120)'"

  # 3. Create the Job and wait for it.
  name="ops-reindex-$(date -u +%Y%m%d%H%M%S)"
  log "creating job/$name with image ${image##*/}"
  reindex_manifest "$name" "$image" | kc create --field-manager=glassbox-ops -f -

  # The Job's conditions, not its pod counts: activeDeadlineSeconds ends it
  # with condition Failed (DeadlineExceeded).
  deadline=$((SECONDS + 960))
  state=running
  while [ "$SECONDS" -lt "$deadline" ]; do
    sleep 5
    line=$(kc -n app get job "$name" -o jsonpath='complete={.status.conditions[?(@.type=="Complete")].status} failed={.status.conditions[?(@.type=="Failed")].status}' 2>/dev/null) || continue
    case "$line" in
      *complete=True*) state=succeeded ;;
      *failed=True*) state=failed ;;
      *) continue ;;
    esac
    break
  done

  # Only the reconcile summaries and the lock or refusal banners are expected
  # here; still redacted, like every other free text.
  section "job/$name log (last 100 lines)"
  kc -n app logs "job/$name" --tail=100 2>&1 | redact 220 || true
  section "job/$name"
  kc -n app get job "$name" || true

  case "$state" in
    succeeded) log "reindex finished; the Job is deleted automatically after a day" ;;
    failed) die "the reindex Job failed (exit 75 means another ingest or reindex held ingest:lock: run it again later)" ;;
    *) die "the reindex Job did not finish within 16 minutes; Kubernetes stops it at 15 (activeDeadlineSeconds)" ;;
  esac
}
main "$@"
