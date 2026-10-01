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
# Actions log. Reads stdin, writes stdout. Two passes:
#
# 1. A multiline pass (awk, state carried from line to line), for secrets
#    whose value is not on the same line as their key:
#    - a key named like password/passwd/token/secret/key/authorization/
#      credential with nothing (or only a YAML |/> block indicator, or a JSON
#      { or [) after its : or = starts a block: the more-indented lines after
#      it collapse to one "<masked>" line; if the next line is not indented
#      and is not itself a key, that one line is masked (`Password:` then
#      `hunter2`);
#    - a data:/stringData:/binaryData: map (kubectl get secret -o yaml/json)
#      keeps its keys but masks every value (short base64 values such as
#      "YWRtaW4=" would slip past the 20-character rule below);
#    - PEM blocks (-----BEGIN ...----- to -----END ...-----) become one
#      "<pem masked>" line; a BEGIN with no END masks the rest of the output.
# 2. The per-line pass (sed), unchanged in spirit:
#    - drops URL userinfo (user:pass@) and query strings,
#    - masks the value after password/passwd/token/secret/key/authorization/
#      credential (key=value, key: value, JSON "key":"value") and Bearer
#      values, and one-line data maps ("data":{...}),
#    - masks JWT-like strings and any run of 20+ base64/hex characters,
#    - truncates every line to max-width characters (default 160).
# Masking happens before truncation so a cut can't leave a token prefix.
#
# Streaming: neither pass buffers the whole input. The awk pass keeps only a
# few state variables, so output still flows line by line (with the usual
# pipe block-buffering, same as the old sed-only filter). All current callers
# feed it finite, short output or a here-string, so nothing waits on it.
# Fail closed: if awk or sed fails (or is missing), a "withheld" line is
# printed instead of the unfiltered rest.
#
# It is a safety net, not a guarantee: prefer printing structured fields
# (names, counts, timestamps) over messages, and use this on what remains.
# Portability: POSIX awk only (gawk on the node and in CI, mawk on Ubuntu
# runners, BSD awk on macOS); character-class spellings instead of sed's I
# flag keep sed working with both GNU and BSD sed.
redact() {
  local width=${1:-160}
  {
    awk -v q="'" '
      function indent_of(s) {
        match(s, /^([ \t]|-[ \t])*/)
        return RLENGTH
      }
      BEGIN {
        kw = "(password|passwd|token|secret|key|authorization|credential)"
        opener_kw = kw "[a-z0-9_.-]*[\"" q "]?[ \t]*[:=][ \t]*([|>][-+0-9]*|[{[])?[ \t\r]*$"
        opener_data = "^([ \t]|-[ \t])*\"?(data|stringdata|binarydata)\"?[ \t]*:[ \t]*[{]?[ \t\r]*$"
        keyline = "^([ \t]|-[ \t])*[\"" q "]?[A-Za-z0-9_./-]+[\"" q "]?[ \t]*[:=]"
        mode = ""
        inpem = 0
      }
      {
        line = $0
        low = tolower(line)
        if (inpem) {
          if (low ~ /-----end[^-]*-----/) inpem = 0
          next
        }
        if (line ~ /^[ \t\r]*$/) {
          print line
          next
        }
        ind = indent_of(line)
        pad = substr(line, 1, ind)
        if (mode == "kw" || mode == "data") {
          if (ind > bind) {
            if (mode == "kw") {
              if (!shown) print pad "<masked>"
              shown = 1
            } else {
              if (cind < 0 || ind < cind) cind = ind
              if (ind == cind) {
                if (match(line, keyline)) print substr(line, 1, RLENGTH) " <masked>"
                else print pad "<masked>"
              }
            }
            next
          }
          if (mode == "kw" && !shown && line !~ keyline) {
            mode = ""
            print pad "<masked>"
            next
          }
          mode = ""
        }
        if (match(low, /-----begin[^-]*-----/)) {
          b = RSTART
          if (match(substr(low, b), /-----end[^-]*-----/)) {
            line = substr(line, 1, b - 1) "<pem masked>" substr(line, b + RSTART + RLENGTH - 1)
            low = tolower(line)
          } else {
            print substr(line, 1, b - 1) "<pem masked>"
            inpem = 1
            next
          }
        }
        if (low ~ opener_data) {
          mode = "data"
          bind = ind
          cind = -1
        } else if (low ~ opener_kw) {
          mode = "kw"
          bind = ind
          shown = 0
        }
        print line
      }
      END {
        if (inpem) print "<pem masked: no END line, rest of output withheld>"
      }
    ' || echo '<redact: filter failed, rest of output withheld>'
  } | {
    sed -E \
      -e 's#(://)[^/@[:space:]]*@#\1<userinfo>@#g' \
      -e 's#\?[^[:space:]"'"'"'<>]*#?<query>#g' \
      -e 's#("?(data|stringData|binaryData)"?[[:space:]]*:[[:space:]]*)\{[^}]*\}#\1{<masked>}#g' \
      -e 's#([bB][eE][aA][rR][eE][rR])[[:space:]]+[^[:space:]]+#\1 <masked>#g' \
      -e 's#([pP][aA][sS][sS][wW][oO][rR][dD]|[pP][aA][sS][sS][wW][dD]|[tT][oO][kK][eE][nN]|[sS][eE][cC][rR][eE][tT]|[kK][eE][yY]|[aA][uU][tT][hH][oO][rR][iI][zZ][aA][tT][iI][oO][nN]|[cC][rR][eE][dD][eE][nN][tT][iI][aA][lL])[A-Za-z0-9_.-]*("|'"'"')?[[:space:]]*[=:][[:space:]]*("[^"]*"|'"'"'[^'"'"']*'"'"'|[^[:space:],;&]+)#\1\2=<masked>#g' \
      -e 's#eyJ[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*#<jwt>#g' \
      -e 's#[A-Za-z0-9+/=_]{20,}#<masked>#g' ||
      echo '<redact: filter failed, rest of output withheld>'
  } | cut -c1-"$width"
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
