# glassbox-ops-scale-keda: scale every KEDA Deployment (operator, metrics
# apiserver, admission webhooks) in the keda namespace to 0 or 1 replicas.
# Argument: 0 | 1.
#
# Scaling to 0 frees roughly 150 MiB on the node. While KEDA is at 0 the
# retrieval-worker stays at its current replica count (its HPA can't read
# queue metrics) and the external.metrics.k8s.io APIService is unavailable,
# which makes some kubectl commands print discovery warnings. To keep it at
# 0 across Flux reconciles, also run flux-suspend for helmrelease-keda (and
# keda); resume both before scaling back to 1.

main() {
  exec </dev/null 2>&1
  set -euo pipefail
  local replicas=${1:-} deployment
  require_one_of "$replicas" 0 1

  if [ "$replicas" = 0 ] && ! flux_is_suspended keda helmreleases.helm.toolkit.fluxcd.io/keda; then
    log "WARNING: HelmRelease keda is not suspended; a Helm upgrade (chart or values change) would scale KEDA back up. Run flux-suspend helmrelease-keda to keep it at 0."
  fi

  log "scaling all deployments in namespace keda to $replicas"
  kc -n keda scale deployment --all --replicas="$replicas"

  if [ "$replicas" = 1 ]; then
    for deployment in $(kc -n keda get deployment -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'); do
      kc_long 300 -n keda rollout status "deployment/$deployment" --timeout=300s ||
        die "deployment/$deployment in keda did not become ready within 5 minutes"
    done
  fi

  kc -n keda get deployment
  kc -n app get scaledobject,hpa 2>/dev/null || true
}
main "$@"
