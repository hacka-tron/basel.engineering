# glassbox-ops-restart-deployment: `kubectl rollout restart` one known
# Deployment and wait for the rollout. Argument: api | retrieval-worker |
# traefik.
#
# api and retrieval-worker roll with maxSurge 0 (k8s/README.md), so api is
# down from the moment its old pod stops until the new one is Ready.

main() {
  exec </dev/null 2>&1
  set -euo pipefail
  local name=${1:-} namespace
  require_one_of "$name" api retrieval-worker traefik
  case "$name" in
    api | retrieval-worker) namespace=app ;;
    traefik) namespace=kube-system ;;
  esac

  log "restarting deployment/$name in namespace $namespace"
  kc -n "$namespace" rollout restart "deployment/$name" --field-manager=glassbox-ops
  # Up to 5 minutes of migration wait plus a 150s startupProbe on api.
  if kc_long 600 -n "$namespace" rollout status "deployment/$name" --timeout=600s; then
    log "rollout of deployment/$name complete"
  else
    kc -n "$namespace" get pods -o wide || true
    die "deployment/$name did not become ready within 10 minutes"
  fi
  kc -n "$namespace" get deployment "$name"
}
main "$@"
