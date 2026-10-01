# glassbox-ops-diagnose: read-only snapshot of the node, the cluster and Flux.
# Changes nothing. Every command is bounded and allowed to fail, so a sick
# node still produces as much of the report as it can.
#
# Output is printed to public GitHub Actions logs (public repository), so it
# must not print secrets: no `kubectl describe secret`, no environment
# dumps, and k3s "Slow SQL" lines are only counted, never printed (kine logs
# the SQL arguments, which can include stored object values).
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
  kc get nodes -o wide || echo "(apiserver did not answer)"
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
  kc get gitrepositories.source.toolkit.fluxcd.io -A 2>/dev/null | cut -c1-200 || true

  section "flux kustomizations"
  kc get kustomizations.kustomize.toolkit.fluxcd.io -A 2>/dev/null | cut -c1-220 || true

  section "flux helmreleases"
  kc get helmreleases.helm.toolkit.fluxcd.io -A 2>/dev/null | cut -c1-220 || true

  section "keda"
  kc -n keda get deploy 2>/dev/null || echo "(no keda namespace)"
  kc -n app get scaledobject,hpa 2>/dev/null || true

  section "warning events (newest 20)"
  kc get events -A --field-selector type=Warning --sort-by=.lastTimestamp 2>/dev/null | tail -20 | cut -c1-200 || true

  section "k3s log, last 30 minutes"
  local k3s_log
  k3s_log=$(timeout 30 journalctl -u k3s --since '-30min' --no-pager -o short-iso 2>/dev/null || true)
  printf 'Slow SQL lines: %s (content not printed)\n' "$(grep -c 'Slow SQL' <<<"$k3s_log" || true)"
  printf 'apiserver/etcd timeout lines: %s\n' "$(grep -ciE 'timeout|deadline exceeded' <<<"$k3s_log" || true)"
  echo "newest 20 error lines:"
  grep -E 'level=error|^[^ ]+ [^ ]+ k3s\[[0-9]+\]: E[0-9]{4} ' <<<"$k3s_log" | grep -v 'Slow SQL' | tail -20 | cut -c1-200 || true

  section "kernel OOM kills, last 6 hours"
  timeout 30 journalctl -k --since '-6h' --no-pager -o short-iso 2>/dev/null | grep -iE 'out of memory|oom-kill|killed process' | tail -8 | cut -c1-200 || true

  section "end of diagnose"
}
main "$@"
