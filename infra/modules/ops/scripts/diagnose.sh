# glassbox-ops-diagnose: read-only snapshot of the node, the cluster and Flux.
# Changes nothing. Every command is bounded and allowed to fail, so a sick
# node still produces as much of the report as it can.
#
# Output is printed to public GitHub Actions logs (public repository), so it
# is safe by construction rather than by hoping nothing sensitive shows up:
#   - no `kubectl describe`, no environment dumps, no object contents;
#   - warning events print only structured fields (namespace, last seen,
#     count, reason, involved object kind and name), never the free-text
#     message;
#   - k3s journal lines are never printed, only counted per category
#     (kine's "Slow SQL" lines include SQL arguments);
#   - the remaining free text (Flux status columns, kernel OOM lines) goes
#     through redact() in lib.sh: URL userinfo and query strings removed,
#     password/token/secret/key/authorization values (also multiline ones,
#     data: maps and PEM blocks) and 20+ character base64/hex/JWT-like
#     strings masked, lines cut to 160-220 characters.
#
# SSM keeps at most 24,000 characters of output, so long lists are capped.

main() {
  exec </dev/null 2>&1

  section "host"
  date -u
  uptime
  uname -r

  section "memory (MiB)"
  free -m

  section "swap devices"
  swapon --show 2>/dev/null || echo "(swapon failed)"
  zramctl 2>/dev/null || echo "(no zram devices)"

  section "pressure stall information (avg10 avg60 avg300)"
  local resource
  for resource in memory io cpu; do
    printf '%-6s %s\n' "$resource" "$(tr '\n' ' ' </proc/pressure/"$resource" 2>/dev/null || echo unavailable)"
  done

  section "vmstat 2s x 3 (si/so = swap in/out KiB/s)"
  vmstat 2 3 2>/dev/null || echo "(vmstat unavailable)"

  section "largest processes by RSS (KiB)"
  ps -eo pid,rss,etimes,comm --sort=-rss | head -12

  section "root disk"
  df -h / | tail -1

  section "k3s service"
  systemctl is-active k3s || true
  systemctl show k3s -p ActiveEnterTimestamp -p NRestarts --no-pager || true

  section "nodes"
  kc get nodes || echo "(apiserver did not answer)"
  kc get nodes -o jsonpath='{range .items[*].status.conditions[*]}{.type}={.status} ({.reason}){"\n"}{end}' || true

  section "kubectl top (metrics-server)"
  kc top node || echo "(metrics unavailable)"
  kc top pod -A --sort-by=memory 2>/dev/null | head -16 || true

  section "pods"
  kc get pods -A -o custom-columns='NS:.metadata.namespace,NAME:.metadata.name,PHASE:.status.phase,READY:.status.containerStatuses[*].ready,RESTARTS:.status.containerStatuses[*].restartCount,AGE:.metadata.creationTimestamp' 2>/dev/null | cut -c1-160 | head -60 || echo "(pods unavailable)"

  section "deployments"
  kc get deploy -A 2>/dev/null | head -25 || true

  section "cronjobs and recent jobs"
  kc get cronjob -A 2>/dev/null || true
  kc get jobs -A --sort-by=.metadata.creationTimestamp 2>/dev/null | tail -6 || true

  section "flux sources"
  kc get gitrepositories.source.toolkit.fluxcd.io -A 2>/dev/null | redact 200 || true

  section "flux kustomizations"
  kc get kustomizations.kustomize.toolkit.fluxcd.io -A 2>/dev/null | redact 220 || true

  section "flux helmreleases"
  kc get helmreleases.helm.toolkit.fluxcd.io -A 2>/dev/null | redact 220 || true

  section "keda"
  kc -n keda get deploy 2>/dev/null || echo "(no keda namespace)"
  kc -n app get scaledobject,hpa 2>/dev/null || true

  section "warning events (newest 20; reason and object only, no messages)"
  kc get events -A --field-selector type=Warning --sort-by=.lastTimestamp \
    -o custom-columns='NS:.metadata.namespace,LAST:.lastTimestamp,COUNT:.count,REASON:.reason,KIND:.involvedObject.kind,NAME:.involvedObject.name' 2>/dev/null |
    tail -21 | redact || true

  section "k3s log, last 30 minutes (counts per category; lines are not printed)"
  local k3s_log errors
  k3s_log=$(timeout 30 journalctl -u k3s --since '-30min' --no-pager -o short-iso 2>/dev/null || true)
  errors=$(grep -E 'level=error|^[^ ]+ [^ ]+ k3s\[[0-9]+\]: E[0-9]{4} ' <<<"$k3s_log" | grep -v 'Slow SQL' || true)
  printf '%-34s %s\n' "Slow SQL lines" "$(grep -c 'Slow SQL' <<<"$k3s_log" || true)"
  printf '%-34s %s\n' "timeout / deadline exceeded" "$(grep -ciE 'timeout|deadline exceeded' <<<"$k3s_log" || true)"
  printf '%-34s %s\n' "error lines (all)" "$(grep -c . <<<"$errors" || true)"
  printf '%-34s %s\n' "  etcd / kine / datastore" "$(grep -ciE 'etcd|kine|sqlite|datastore' <<<"$errors" || true)"
  printf '%-34s %s\n' "  connection refused / reset" "$(grep -ciE 'connection (refused|reset)|broken pipe' <<<"$errors" || true)"
  printf '%-34s %s\n' "  certificate / TLS" "$(grep -ciE 'x509|certificate|tls' <<<"$errors" || true)"
  printf '%-34s %s\n' "  image pull / registry" "$(grep -ciE 'imagepull|errimage|pull access|registry' <<<"$errors" || true)"
  printf '%-34s %s\n' "  memory / OOM / eviction" "$(grep -ciE 'oom|out of memory|evict|memory' <<<"$errors" || true)"
  printf '%-34s %s\n' "  disk / no space" "$(grep -ciE 'no space|disk|ephemeral-storage' <<<"$errors" || true)"
  printf '%-34s %s\n' "  probe / readiness / liveness" "$(grep -ciE 'probe|readiness|liveness' <<<"$errors" || true)"

  section "kernel OOM kills, last 6 hours"
  timeout 30 journalctl -k --since '-6h' --no-pager -o short-iso 2>/dev/null | grep -iE 'out of memory|oom-kill|killed process' | tail -8 | redact 200 || true

  section "end of diagnose"
}
main "$@"
