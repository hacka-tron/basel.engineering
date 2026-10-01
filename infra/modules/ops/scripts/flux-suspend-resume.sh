# glassbox-ops-flux-suspend / glassbox-ops-flux-resume: set spec.suspend on
# one known Flux object, exactly like `flux suspend` / `flux resume`.
# Arguments: suspend|resume, then the target:
#   keda | keda-scaling | ingest | app-ready | flux-system  (Kustomizations in
#                                                           flux-system)
#   helmrelease-keda                                        (HelmRelease keda
#                                                           in keda)
#
# The Kustomization manifests in Git don't set spec.suspend, so the root
# flux-system Kustomization doesn't own that field and won't undo it.

main() {
  exec </dev/null 2>&1
  set -euo pipefail
  local verb=${1:-} target=${2:-} namespace resource value
  require_one_of "$verb" suspend resume
  require_one_of "$target" keda keda-scaling ingest app-ready flux-system helmrelease-keda
  case "$target" in
    helmrelease-keda)
      namespace=keda
      resource=helmreleases.helm.toolkit.fluxcd.io/keda
      ;;
    *)
      namespace=flux-system
      resource=kustomizations.kustomize.toolkit.fluxcd.io/$target
      ;;
  esac
  if [ "$verb" = suspend ]; then value=true; else value=false; fi

  if [ "$target" = flux-system ] && [ "$verb" = suspend ]; then
    log "WARNING: suspending the root flux-system Kustomization stops ALL deploys (new images, manifest changes) until flux-resume flux-system."
  fi

  log "$verb $namespace/$resource"
  kc -n "$namespace" patch "$resource" --type=merge --field-manager=glassbox-ops \
    -p "{\"spec\": {\"suspend\": $value} }"

  if [ "$verb" = resume ]; then
    # Like `flux resume`: ask for a reconcile now and report how it went.
    # Not fatal on timeout: the object is resumed either way.
    flux_request_reconcile "$namespace" "$resource" 300 || true
  fi

  section "flux objects now"
  kc get kustomizations.kustomize.toolkit.fluxcd.io -A | redact 200 || true
  kc get helmreleases.helm.toolkit.fluxcd.io -A | redact 200 || true
}
main "$@"
