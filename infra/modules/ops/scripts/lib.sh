#!/usr/bin/env bash
# Shared helpers for the glassbox-ops-* SSM Command documents
# (infra/modules/ops/main.tf). main.tf concatenates this file in front of one
# action script and pipes the result into `bash -s` on the node, as root.
# Never run on its own, and never edited on the node.
#
# Rules for every script in this directory (main.tf enforces the first two):
#   - no double curly braces anywhere: SSM would treat them as a document
#     parameter reference;
#   - no line that is exactly GLASSBOX_OPS (the heredoc terminator);
#   - everything runs inside main(), so bash parses the whole script before
#     running any of it (stdin is the script itself);
#   - inputs arrive as positional arguments that SSM has already checked
#     against the document's allowedValues; scripts re-check them anyway.

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

log() { printf '[ops %s] %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
die() {
  log "ERROR: $*"
  exit 1
}
section() { printf '\n===== %s =====\n' "$*"; }

# redact [max-width]: filter for any free text that reaches the (public)
# Actions log. Reads stdin, writes stdout. It
#   - drops URL userinfo (user:pass@) and query strings,
#   - masks the value after password/passwd/token/secret/key/authorization/
#     credential (key=value, key: value, JSON "key":"value") and Bearer values,
#   - masks JWT-like strings and any run of 20+ base64/hex characters,
#   - truncates every line to max-width characters (default 160).
# Masking happens before truncation so a cut can't leave a token prefix.
# It is a safety net, not a guarantee: prefer printing structured fields
# (names, counts, timestamps) over messages, and use this on what remains.
# Character-class spellings instead of sed's I flag keep it working with
# both GNU and BSD sed (the offline test runs on either).
redact() {
  local width=${1:-160}
  sed -E \
    -e 's#(://)[^/@[:space:]]*@#\1<userinfo>@#g' \
    -e 's#\?[^[:space:]"'"'"'<>]*#?<query>#g' \
    -e 's#([bB][eE][aA][rR][eE][rR])[[:space:]]+[^[:space:]]+#\1 <masked>#g' \
    -e 's#([pP][aA][sS][sS][wW][oO][rR][dD]|[pP][aA][sS][sS][wW][dD]|[tT][oO][kK][eE][nN]|[sS][eE][cC][rR][eE][tT]|[kK][eE][yY]|[aA][uU][tT][hH][oO][rR][iI][zZ][aA][tT][iI][oO][nN]|[cC][rR][eE][dD][eE][nN][tT][iI][aA][lL])[A-Za-z0-9_.-]*("|'"'"')?[[:space:]]*[=:][[:space:]]*("[^"]*"|'"'"'[^'"'"']*'"'"'|[^[:space:],;&]+)#\1\2=<masked>#g' \
    -e 's#eyJ[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*#<jwt>#g' \
    -e 's#[A-Za-z0-9+/=_]{20,}#<masked>#g' |
    cut -c1-"$width"
}

# kubectl with hard upper bounds: on a node thrashing in swap the apiserver
# can accept a request and then never answer it.
kc() { timeout --kill-after=10 90 k3s kubectl --request-timeout=60s "$@"; }

# Same, for long waits (rollout status, kubectl wait).
kc_long() {
  local seconds=$1
  shift
  timeout --kill-after=10 "$((seconds + 30))" k3s kubectl --request-timeout=0 "$@"
}

require_one_of() {
  local value=$1 allowed
  shift
  for allowed in "$@"; do
    [ "$value" = "$allowed" ] && return 0
  done
  die "unexpected value '$value' (allowed: $*)"
}

# Ask Flux to reconcile an object now (what `flux reconcile` does): set the
# requestedAt annotation, then wait until the controller reports it handled
# that exact request. Prints the result; returns 1 on timeout.
flux_request_reconcile() {
  local namespace=$1 resource=$2 wait_seconds=$3 token handled deadline
  token="glassbox-ops-$(date -u +%Y%m%dT%H%M%SZ)"
  kc -n "$namespace" annotate --overwrite --field-manager=glassbox-ops \
    "$resource" "reconcile.fluxcd.io/requestedAt=$token" >/dev/null || return 1
  [ "$wait_seconds" -gt 0 ] || return 0
  deadline=$((SECONDS + wait_seconds))
  while [ "$SECONDS" -lt "$deadline" ]; do
    handled=$(kc -n "$namespace" get "$resource" -o jsonpath='{.status.lastHandledReconcileAt}' 2>/dev/null || true)
    if [ "$handled" = "$token" ]; then
      log "$namespace/$resource reconciled: $(kc -n "$namespace" get "$resource" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status} {.status.conditions[?(@.type=="Ready")].message}' 2>/dev/null | redact 200)"
      return 0
    fi
    sleep 5
  done
  log "WARNING: $namespace/$resource did not finish reconciling within ${wait_seconds}s (it may still be working)"
  return 1
}

flux_is_suspended() {
  [ "$(kc -n "$1" get "$2" -o jsonpath='{.spec.suspend}' 2>/dev/null)" = "true" ]
}
