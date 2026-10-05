# glassbox-ops-diagnose: read-only snapshot of the node, the cluster and Flux,
# plus counts of the API's CSP Report-Only violation log lines.
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
#     SELECT counts (query log, last ingest run's sweep summary, TTFT
#     p50/p95); the password comes from the mysql container's own
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
  local today yesterday cap warm_cap cap_label warm_cap_label
  today=$(date -u +%F)
  yesterday=$(date -u -d yesterday +%F)
  # An unreadable ConfigMap must not look like "budget spent": fall back to
  # the code defaults (limits.py 100, warm.py 10) and say so.
  cap=$(kc -n app get configmap glassbox-config -o jsonpath='{.data.GLASSBOX_DAILY_LLM_CAP}' 2>/dev/null)
  if [[ $cap =~ ^[0-9]+$ ]]; then cap_label=$cap; else cap=100 cap_label="default 100 (configmap unreadable)"; fi
  warm_cap=$(kc -n app get configmap glassbox-config -o jsonpath='{.data.GLASSBOX_WARM_DAILY_LLM_CAP}' 2>/dev/null)
  if [[ $warm_cap =~ ^[0-9]+$ ]]; then warm_cap_label=$warm_cap; else warm_cap_label="default 10 (configmap unreadable)"; fi

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
GET corpus:ver:portfolio
EOF
  )
  if [ "${#replies[@]}" -lt 12 ]; then
    echo "(redis did not answer: ${#replies[@]} of 12 replies)"
  else
    local day answers rewrites used left
    printf 'cap: %s answers/day (= %s quarter-units; answer 4, follow-up rewrite 1)\n' \
      "$cap_label" "$((cap * 4))"
    for day in today yesterday; do
      if [ "$day" = today ]; then
        answers=$(as_int "${replies[0]}") rewrites=$(as_int "${replies[1]}")
      else
        answers=$(as_int "${replies[3]}") rewrites=$(as_int "${replies[4]}")
      fi
      if [[ "$answers$rewrites" == *"?"* ]]; then
        used="?" left="?"
      else
        used=$((answers * 4 + rewrites))
        left=$((cap * 4 - used))
      fi
      printf '%-9s %s: answers %s, rewrite units %s, used %s of %s units, %s left\n' \
        "$day" "$([ "$day" = today ] && echo "$today" || echo "$yesterday")" \
        "$answers" "$rewrites" "$used" "$((cap * 4))" "$left"
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
      "$(as_int "${replies[7]}")" "$(as_int "${replies[8]}")" "$warm_cap_label"
    printf 'corpus versions (bumped per changed document; older cached answers go cold): about_me %s, about_system %s, portfolio %s\n' \
      "$(as_int "${replies[9]}")" "$(as_int "${replies[10]}")" "$(as_int "${replies[11]}")"
  fi

  # Rate-limit buckets touched in the last 10 minutes (rl:<salted hash of
  # the client IP>, limits.py). Fewest tokens left = busiest client lately.
  local -a buckets=()
  while IFS= read -r reply; do buckets+=("$reply"); done < <(
    kc -n data exec redis-0 -c redis -- redis-cli --raw --scan --pattern 'rl:*' --count 1000 2>/dev/null |
      grep -E '^rl:[0-9a-f]{64}$' | head -201
  )
  local bucket_count=${#buckets[@]}
  if [ "$bucket_count" -gt 200 ]; then
    buckets=("${buckets[@]:0:200}")
    bucket_count="200+ (200 shown)"
  fi
  printf 'rate-limit buckets active in the last 10 min: %s (10 asks per 10 min each)\n' "$bucket_count"
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

  # Today's query log (UTC), the last ingest run and TTFT, counts only.
  echo "query log today (UTC), last ingest run, TTFT:"
  # The variables expand inside the mysql container, from its own env.
  # --force: a missing table or column fails only its own statement.
  # shellcheck disable=SC2016
  query_log_sql | kc -n data exec -i mysql-0 -c mysql -- sh -c \
    'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql --connect-timeout=10 --force -N -B -u "$MYSQL_USER" "$MYSQL_DATABASE"' \
    2>&1 | redact 160 | head -60 || true
}

# The read-only SQL of the query-log block (SELECT only; question text, IPs
# and hashes are never selected, only counts and integers). Its own function
# so the test can run exactly this text against a local MySQL.
query_log_sql() {
  cat <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SET SESSION max_execution_time = 10000;
SET time_zone = '+00:00';
SELECT CONCAT('  by mode/cache: ', mode, ' ', cache_status, ' = ', COUNT(*))
  FROM queries WHERE created_at >= UTC_DATE() GROUP BY mode, cache_status ORDER BY mode, cache_status;
SELECT CONCAT('  asks: ', COUNT(*), ', follow-ups (turn > 0): ', COALESCE(SUM(turn_index > 0), 0), ', generated answers (full, miss): ', COALESCE(SUM(mode = 'full' AND cache_status = 'miss'), 0))
  FROM queries WHERE created_at >= UTC_DATE();
SELECT CONCAT('  hour ', LPAD(HOUR(created_at), 2, '0'), 'Z: ', COUNT(*), ' asks, ', SUM(mode = 'full' AND cache_status = 'miss'), ' generated, ', SUM(mode = 'retrieval_only'), ' retrieval_only, ', SUM(turn_index > 0), ' follow-ups')
  FROM queries WHERE created_at >= UTC_DATE() GROUP BY HOUR(created_at) ORDER BY HOUR(created_at);
SELECT CONCAT('  most repeated question #', ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC), ': asked ', COUNT(*), ' times (', MIN(corpus), ', ', SUM(mode = 'full' AND cache_status = 'miss'), ' generated)')
  FROM queries WHERE created_at >= UTC_DATE() GROUP BY question ORDER BY COUNT(*) DESC LIMIT 3;
SELECT '  last ingest run: none recorded' FROM (SELECT 1) d WHERE NOT EXISTS (SELECT 1 FROM ingestion_runs);
SELECT CONCAT('  last ingest run #', id, ': ', status, ', started ', TIMESTAMPDIFF(MINUTE, started_at, NOW()), ' min ago, docs changed ', COALESCE(docs_changed, 0), ', chunks written ', COALESCE(chunks_written, 0), ', sweep mode ', CASE WHEN JSON_EXTRACT(notes, '$.sweep.mode') IS NULL THEN 'none' WHEN JSON_UNQUOTE(JSON_EXTRACT(notes, '$.sweep.mode')) REGEXP '^[a-z_]{1,12}$' THEN JSON_UNQUOTE(JSON_EXTRACT(notes, '$.sweep.mode')) ELSE '?' END, ', corpora in sweep ', COALESCE(JSON_LENGTH(JSON_EXTRACT(notes, '$.sweep.corpora')), 0))
  FROM ingestion_runs ORDER BY id DESC LIMIT 1;
SELECT CONCAT('    sweep ', CASE WHEN jt.corpus REGEXP '^[a-z_]{1,20}$' THEN jt.corpus ELSE '?' END, ': known ', COALESCE(jt.known, '?'), ', planned ', COALESCE(jt.planned, '?'), ', deleted ', COALESCE(jt.deleted, '?'), ', ', CASE WHEN jt.refused IS NULL OR JSON_TYPE(jt.refused) = 'NULL' THEN 'not refused' ELSE 'REFUSED (reason in ingestion_runs.notes)' END)
  FROM (SELECT notes FROM ingestion_runs ORDER BY id DESC LIMIT 1) r,
  JSON_TABLE(r.notes, '$.sweep.corpora[*]' COLUMNS (corpus VARCHAR(40) PATH '$.corpus', known INT PATH '$.known', planned INT PATH '$.planned', deleted INT PATH '$.deleted', refused JSON PATH '$.refused')) jt;
SELECT '  ttft last 24h: no asks with a ttft_ms' FROM (SELECT 1) d
  WHERE NOT EXISTS (SELECT 1 FROM queries WHERE created_at >= NOW() - INTERVAL 1 DAY AND ttft_ms IS NOT NULL);
SELECT CONCAT('  ttft last 24h, cache_status ', cache_status, ': n ', MAX(n), ', p50 ', MAX(IF(rn = CEIL(n * 0.50), ttft_ms, NULL)), ' ms, p95 ', MAX(IF(rn = CEIL(n * 0.95), ttft_ms, NULL)), ' ms')
  FROM (SELECT cache_status, ttft_ms, ROW_NUMBER() OVER (PARTITION BY cache_status ORDER BY ttft_ms) AS rn, COUNT(*) OVER (PARTITION BY cache_status) AS n
          FROM queries WHERE created_at >= NOW() - INTERVAL 1 DAY AND ttft_ms IS NOT NULL) ranked
  GROUP BY cache_status ORDER BY cache_status;
SQL
}

# CSP Report-Only lines, logged by /api/csp-report
# (services/glassbox/api/csp_report.py) in a fixed, already-sanitized shape.
# Only counts and the directive/blocked-origin pairs are printed. Covers the
# running api pod only (its log resets on restart or deploy).
csp_section() {
  section "CSP Report-Only violations, api log last 24 hours (count, directive, blocked origin)"
  local csp_log
  csp_log=$(kc -n app logs deploy/api -c api --since=24h 2>/dev/null | grep -F 'csp-report-only violation' || true)
  printf '%-34s %s\n' "violation lines" "$(grep -c 'violation directive=' <<<"$csp_log" || true)"
  printf '%-34s %s\n' "oversize reports dropped" "$(grep -c 'violation oversize' <<<"$csp_log" || true)"
  printf '%-34s %s\n' "log-cap summary lines" "$(grep -c 'violation log cap' <<<"$csp_log" || true)"
  sed -nE 's/.*csp-report-only violation directive=([a-z-]+) blocked=([A-Za-z0-9:./-]+) path=.*/\1 \2/p' <<<"$csp_log" |
    sort | uniq -c | sort -rn | head -15 | redact 160 || true
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

  csp_section

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

  # Last, and bounded as a whole, so a slow Redis or MySQL exec can't push
  # the node sections above past the document's 300 s limit.
  section "LLM budget, kill switch, warm-up cap, query log, last ingest run, TTFT (counts only)"
  timeout --kill-after=5 60 bash -c "$(declare -f kc redact as_int redis_read budget_section); budget_section" ||
    echo "(budget section did not finish within 60s)"

  section "end of diagnose"
}
main "$@"
