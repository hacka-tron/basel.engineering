# glassbox-ops-flux-reconcile: make Flux fetch the deploy branch and apply
# it now instead of waiting for the next interval (`flux reconcile
# kustomization flux-system --with-source`), then nudge every other
# non-suspended Kustomization and HelmRelease. No arguments.

main() {
  exec </dev/null 2>&1
  set -euo pipefail
  local ks hr name namespace

  if flux_is_suspended flux-system kustomizations.kustomize.toolkit.fluxcd.io/flux-system; then
    die "the root flux-system Kustomization is suspended; run flux-resume with target flux-system instead"
  fi

  log "fetching the deploy branch (gitrepository/flux-system)"
  flux_request_reconcile flux-system gitrepositories.source.toolkit.fluxcd.io/flux-system 180 ||
    die "source did not refresh; see the diagnose output"

  log "applying the root Kustomization (flux-system)"
  flux_request_reconcile flux-system kustomizations.kustomize.toolkit.fluxcd.io/flux-system 420 ||
    die "root Kustomization did not finish within 7 minutes"

  for ks in $(kc -n flux-system get kustomizations.kustomize.toolkit.fluxcd.io -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'); do
    [ "$ks" = flux-system ] && continue
    if flux_is_suspended flux-system "kustomizations.kustomize.toolkit.fluxcd.io/$ks"; then
      log "skipping suspended Kustomization $ks"
      continue
    fi
    log "requesting reconcile of Kustomization $ks (not waiting)"
    flux_request_reconcile flux-system "kustomizations.kustomize.toolkit.fluxcd.io/$ks" 0 || true
  done

  for hr in $(kc get helmreleases.helm.toolkit.fluxcd.io -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}'); do
    namespace=${hr%%/*}
    name=${hr#*/}
    if flux_is_suspended "$namespace" "helmreleases.helm.toolkit.fluxcd.io/$name"; then
      log "skipping suspended HelmRelease $hr"
      continue
    fi
    log "requesting reconcile of HelmRelease $hr (not waiting)"
    flux_request_reconcile "$namespace" "helmreleases.helm.toolkit.fluxcd.io/$name" 0 || true
  done

  section "flux objects now"
  kc get gitrepositories.source.toolkit.fluxcd.io -A | redact 200 || true
  kc get kustomizations.kustomize.toolkit.fluxcd.io -A | redact 200 || true
  kc get helmreleases.helm.toolkit.fluxcd.io -A | redact 200 || true
}
main "$@"
