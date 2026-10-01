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
# The "LLM budget" section reads Redis counters and MySQL query-log counts
# through kubectl exec into data/redis-0 and data/mysql-0, read-only:
#   - Redis: only GET, TTL, EXISTS and HGET (redis_read refuses anything
#     else) plus a SCAN of rate-limit bucket names; values are printed only
#     after checking they are numbers;
#   - MySQL: one read-only session (SET SESSION TRANSACTION READ ONLY) of
#     SELECT counts; the password comes from the mysql container's own
#     environment inside the exec (MYSQL_PWD), so it is never on a command
#     line, in this script or in the output; question text, IPs and hashes
#     are never selected (only counts).
# No Secret object is read.
#
# SSM keeps at most 24,000 characters of output, so long lists are capped.

# Print $1 if it is an integer, else "?" (an empty Redis reply means unset: 0).
as_int() {
  if [ -z "$1" ]; then
    echo 0
  elif [[ $1 =~ ^-?[0-9]+$ ]]; then
    echo "$1"
  else
    echo "?"
  fi
}

# Read-only redis-cli: commands on stdin, one reply per line on stdout.
# Refuses (prints nothing, returns 1) unless every command is GET, TTL,
# EXISTS or HGET on a plain key name.
redis_read() {
  local commands line
  commands=$(cat)
  while IFS= read -r line; do
    if ! [[ $line =~ ^(GET|TTL|EXISTS|HGET)\ [A-Za-z0-9:_.-]+(\ [A-Za-z0-9_]+)?$ ]]; then
      echo "(redis_read: refused a non read-only command)" >&2
      return 1
    fi
  done <<<"$commands"
  kc -n data exec -i redis-0 -c redis -- redis-cli --raw <<<"$commands"
}

# Daily LLM budget (services/glassbox/limits.py), kill switch
# (services/glassbox/killswitch.py) and warm-up cap (services/glassbox/warm.py).
budget_section() {
  local today yesterday cap warm_cap
  today=$(date -u +%F)
  yesterday=$(date -u -d yesterday +%F)
  cap=$(as_int "$(kc -n app get configmap glassbox-config -o jsonpath='{.data.GLASSBOX_DAILY_LLM_CAP}' 2>/dev/null)")
  warm_cap=$(as_int "$(kc -n app get configmap glassbox-config -o jsonpath='{.data.GLASSBOX_WARM_DAILY_LLM_CAP}' 2>/dev/null)")

  local -a replies=()
  local reply
  while IFS= read -r reply; do replies+=("$reply"); done < <(
    redis_read <<EOF
GET budget:llm:$today
GET budget:llm:rw:$today
TTL budget:llm:$today
GET budget:llm:$yesterday
GET budget:llm:rw:$yesterday
EXISTS glassbox:kill:disable_llm
GET glassbox:kill:disable_llm
GET warm:budget:$today
GET warm:budget:$yesterday
GET corpus:ver:about_me
GET corpus:ver:about_system
EOF
  )
  if [ "${#replies[@]}" -lt 11 ]; then
    echo "(redis did not answer: ${#replies[@]} of 11 replies)"
  else
    local day answers rewrites used left
    printf 'configured cap: %s answers/day (= %s quarter-units; answer 4, follow-up rewrite 1)\n' \
      "$cap" "$([ "$cap" = "?" ] && echo "?" || echo $((cap * 4)))"
    for day in today yesterday; do
      if [ "$day" = today ]; then
        answers=$(as_int "${replies[0]}") rewrites=$(as_int "${replies[1]}")
      else
        answers=$(as_int "${replies[3]}") rewrites=$(as_int "${replies[4]}")
      fi
      if [[ "$answers$rewrites$cap" == *"?"* ]]; then
        used="?" left="?"
      else
        used=$((answers * 4 + rewrites))
        left=$((cap * 4 - used))
      fi
      printf '%-9s %s: answers %s, rewrite units %s, used %s of %s units, %s left\n' \
        "$day" "$([ "$day" = today ] && echo "$today" || echo "$yesterday")" \
        "$answers" "$rewrites" "$used" "$([ "$cap" = "?" ] && echo "?" || echo $((cap * 4)))" "$left"
    done
    printf 'today counter expires in: %ss (-2 = not created yet)\n' "$(as_int "${replies[2]}")"
    local kill_state
    case "$(as_int "${replies[5]}"):${replies[6]}" in
      0:*) kill_state="off (flag not set)" ;;
      1:1) kill_state="ON: LLM disabled, every answer is retrieval_only" ;;
      1:*) kill_state="flag present but not \"1\" (ignored by the api: off)" ;;
      *) kill_state="unknown" ;;
    esac
    printf 'kill switch glassbox:kill:disable_llm: %s\n' "$kill_state"
    printf 'warm-up LLM calls: today %s, yesterday %s (cap %s/day, part of the cap above)\n' \
      "$(as_int "${replies[7]}")" "$(as_int "${replies[8]}")" "$warm_cap"
    printf 'corpus versions (bumped per changed document; older cached answers go cold): about_me %s, about_system %s\n' \
      "$(as_int "${replies[9]}")" "$(as_int "${replies[10]}")"
  fi

  # Rate-limit buckets touched in the last 10 minutes (rl:<salted hash of
  # the client IP>, limits.py). Fewest tokens left = busiest client lately.
  local -a buckets=()
  while IFS= read -r reply; do buckets+=("$reply"); done < <(
    kc -n data exec redis-0 -c redis -- redis-cli --raw --scan --pattern 'rl:*' --count 1000 2>/dev/null |
      grep -E '^rl:[0-9a-f]{64}$' | head -200
  )
  printf 'rate-limit buckets active in the last 10 min: %s (10 asks per 10 min each)\n' "${#buckets[@]}"
  if [ "${#buckets[@]}" -gt 0 ]; then
    local -a tokens=()
    while IFS= read -r reply; do tokens+=("$reply"); done < <(printf 'HGET %s tokens\n' "${buckets[@]}" | redis_read)
    local i
    for i in "${!buckets[@]}"; do
      [[ ${tokens[$i]:-} =~ ^[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?$ ]] || continue
      printf '%s %s\n' "${tokens[$i]}" "${buckets[$i]:3:8}"
    done | sort -g | head -3 | while read -r left prefix; do
      printf '  client %s...: %.1f of 10 asks left\n' "$prefix" "$left"
    done
  fi

  # Today's query log (UTC), counts only.
  echo "query log today (UTC):"
  # The variables expand inside the mysql container, from its own env.
  # shellcheck disable=SC2016
  kc -n data exec -i mysql-0 -c mysql -- sh -c \
    'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql --connect-timeout=10 -N -B -u "$MYSQL_USER" "$MYSQL_DATABASE"' \
    2>&1 <<'SQL' | redact 160 | head -45 || true
SET SESSION TRANSACTION READ ONLY;
SET SESSION max_execution_time = 10000;
SET time_zone = '+00:00';
SELECT CONCAT('  by mode/cache: ', mode, ' ', cache_status, ' = ', COUNT(*))
  FROM queries WHERE created_at >= UTC_DATE() GROUP BY mode, cache_status ORDER BY mode, cache_status;
SELECT CONCAT('  asks: ', COUNT(*), ', follow-ups (turn > 0): ', COALESCE(SUM(turn_index > 0), 0), ', generated answers (full, miss): ', COALESCE(SUM(mode = 'full' AND cache_status = 'miss'), 0))
  FROM queries WHERE created_at >= UTC_DATE();
SELECT CONCAT('  hour ', LPAD(HOUR(created_at), 2, '0'), 'Z: ', COUNT(*), ' asks, ', SUM(mode = 'full' AND cache_status = 'miss'), ' generated, ', SUM(mode = 'retrieval_only'), ' retrieval_only, ', SUM(turn_index > 0), ' follow-ups')
  FROM queries WHERE created_at >= UTC_DATE() GROUP BY HOUR(created_at) ORDER BY HOUR(created_at);
SELECT CONCAT('  most repeated question: asked ', COUNT(*), ' times (', MIN(corpus), ', ', SUM(mode = 'full' AND cache_status = 'miss'), ' generated)')
  FROM queries WHERE created_at >= UTC_DATE() GROUP BY question ORDER BY COUNT(*) DESC LIMIT 3;
SQL
}

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

  section "LLM budget, kill switch, warm-up cap, query log (counts only)"
  budget_section

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
