#!/usr/bin/env bash
# Offline test for the "LLM budget" and "CSP Report-Only" sections of
# ../scripts/diagnose.sh, with a stubbed kubectl. Checks the numbers it prints, that Redis is only asked
# read-only commands, that no Secret is read, and that MySQL output passes
# through redact(). Needs no cluster or AWS.
#   bash infra/modules/ops/tests/diagnose-budget-test.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=../scripts/lib.sh
source "$here/../scripts/lib.sh"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# diagnose.sh ends by calling main; load its functions without running it.
grep -vxF 'main "$@"' "$here/../scripts/diagnose.sh" >"$work/diagnose-functions.sh"
# shellcheck source=../scripts/diagnose.sh
source "$work/diagnose-functions.sh"
calls=$work/calls

# GNU date's -d is not on macOS; the section only needs these two forms.
date() {
  case "$*" in
    "-u +%F") echo 2026-10-01 ;;
    "-u -d yesterday +%F") echo 2026-09-30 ;;
    *) command date "$@" ;;
  esac
}

KILL_EXISTS=0
KILL_VALUE=
CONFIGMAP_OK=1
# Stub kubectl: logs every call (and any stdin) and answers like the cluster.
kc() {
  local input="" all="$*"
  if [[ $all == *"exec -i"* ]]; then input=$(cat); fi
  printf '%s\n' "$all" >>"$calls"
  [ -z "$input" ] || printf '  stdin: %s\n' "${input//$'\n'/$'\n'  stdin: }" >>"$calls"
  case "$*" in
    *GLASSBOX_DAILY_LLM_CAP*) [ "$CONFIGMAP_OK" = 1 ] && echo 100 ;;
    *GLASSBOX_WARM_DAILY_LLM_CAP*) [ "$CONFIGMAP_OK" = 1 ] && echo 10 ;;
    *"--scan --pattern rl:*"*)
      if [ -n "${BUCKETS:-}" ]; then
        local n
        for ((n = 0; n < BUCKETS; n++)); do printf 'rl:%064x\n' "$n"; done
        return
      fi
      echo "rl:$(printf 'a%.0s' {1..64})"
      echo "rl:$(printf 'b%.0s' {1..64})"
      echo "rl:not-a-hash"
      # /api/csp-report's own buckets are not question buckets.
      echo "rl:csp:$(printf 'c%.0s' {1..64})"
      ;;
    *redis-cli*)
      local line
      while IFS= read -r line; do
        case "$line" in
          "GET budget:llm:2026-10-01") echo 27 ;;
          "GET budget:llm:rw:2026-10-01") echo 9 ;;
          "TTL budget:llm:2026-10-01") echo 160000 ;;
          "GET budget:llm:2026-09-30") echo 12 ;;
          "GET budget:llm:rw:2026-09-30") echo ;;
          "EXISTS glassbox:kill:disable_llm") echo "$KILL_EXISTS" ;;
          "GET glassbox:kill:disable_llm") echo "$KILL_VALUE" ;;
          "GET warm:budget:2026-10-01") echo 10 ;;
          "GET warm:budget:2026-09-30") echo 4 ;;
          "GET corpus:ver:about_me") echo 3 ;;
          "GET corpus:ver:about_system") echo 41 ;;
          "GET corpus:ver:portfolio") echo 2 ;;
          "HGET rl:a"*) echo 2.5 ;;
          "HGET rl:b"*) echo 9 ;;
          "HGET rl:0"*) echo 10 ;;
          *) echo "UNEXPECTED $line" ;;
        esac
      done <<<"$input"
      ;;
    *"logs deploy/api"*)
      printf '%s\n' \
        "INFO: 10.42.0.8:1234 - \"POST /api/csp-report HTTP/1.1\" 204" \
        "csp-report-only violation directive=style-src-attr blocked=inline path=/" \
        "csp-report-only violation directive=style-src-attr blocked=inline path=/" \
        "csp-report-only violation directive=connect-src blocked=https://cdn.example.com path=/" \
        "csp-report-only violation directive=img-src blocked=chrome-extension: path=/" \
        "csp-report-only violation oversize: body over 8192 bytes, dropped" \
        "csp-report-only violation log cap: 7 more reports not logged in the last minute" \
        "csp-report-only violation directive=script-src blocked=https://x.example/?token=ABCDEFGHIJKLMNOPQRSTUVWXYZ path=/"
      ;;
    *mysql*)
      printf '%s\n' "  by mode/cache: full miss = 31" "  asks: 40, follow-ups (turn > 0): 6, generated answers (full, miss): 31"
      echo "ERROR 1045 (28000): Access denied, password=hunter2xyz" # pragma: allowlist secret
      ;;
  esac
}

fail=0
check() { # check <label> <condition-exit-status>
  if [ "$2" -eq 0 ]; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi
}

out=$(budget_section 2>&1)
printf '%s\n' "$out" | sed 's/^/     | /'

grep -qF 'cap: 100 answers/day (= 400 quarter-units' <<<"$out"
check "cap from the ConfigMap" $?
grep -qF 'today     2026-10-01: answers 27, rewrite units 9, used 117 of 400 units, 283 left' <<<"$out"
check "today's counters and units" $?
grep -qF 'yesterday 2026-09-30: answers 12, rewrite units 0, used 48 of 400 units, 352 left' <<<"$out"
check "yesterday's counters (missing key = 0)" $?
grep -qF 'kill switch glassbox:kill:disable_llm: off (flag not set)' <<<"$out"
check "kill switch off" $?
grep -qF 'warm-up LLM calls: today 10, yesterday 4 (cap 10/day' <<<"$out"
check "warm-up counters" $?
grep -qF 'about_me 3, about_system 41, portfolio 2' <<<"$out"
check "corpus versions" $?
grep -qF 'rate-limit buckets active in the last 10 min: 2' <<<"$out"
check "bucket count ignores malformed names and rl:csp: buckets" $?
! grep -qF 'cccccccc' <<<"$out"
check "rl:csp: bucket never listed as a client" $?
first=$(grep -m1 '  client ' <<<"$out")
grep -qxF '  client aaaaaaaa...: 2.5 of 10 asks left' <<<"$first"
check "busiest bucket first, hash cut to 8 characters" $?
! grep -qE '[0-9a-f]{9,}' <<<"$out"
check "no hash longer than 8 characters printed" $?
grep -qF 'generated answers (full, miss): 31' <<<"$out"
check "query-log counts printed" $?
! grep -qF hunter2xyz <<<"$out"
check "mysql output redacted" $?
! grep -qF UNEXPECTED <<<"$out"
check "every redis command answered" $?

# The shipped path: main runs budget_section in `bash -c` with only the
# functions in BUDGET_FUNCTIONS, so a helper missing from that list breaks it.
export calls KILL_EXISTS KILL_VALUE CONFIGMAP_OK
sub=$(bash -c "$(declare -f $BUDGET_FUNCTIONS); budget_section" 2>&1)
! grep -qF 'command not found' <<<"$sub"
check "subshell: every helper is available (no command not found)" $?
grep -qF 'by mode/cache: full miss = 31' <<<"$sub" && grep -qF 'generated answers (full, miss): 31' <<<"$sub"
check "subshell: query-log counts still printed" $?

# Read-only: no Secret reads, and Redis only gets GET/TTL/EXISTS/HGET/SCAN.
! grep -qiE 'secret' "$calls"
check "no Secret read" $?
# Redis lines: only the read commands. MySQL: every statement is a SELECT or
# one of the three session SETs, and no write keyword appears anywhere.
stdin_lines=$(sed -n 's/^  stdin: //p' "$calls")
grep -q . <<<"$stdin_lines" &&
  ! grep -E '^(GET|TTL|EXISTS|HGET) ' <<<"$stdin_lines" | grep -vE '^(GET|TTL|EXISTS|HGET) [A-Za-z0-9:_.-]+( tokens)?$' | grep -q .
check "redis gets only read commands" $?
sql=$(grep -vE '^(GET|TTL|EXISTS|HGET) ' <<<"$stdin_lines")
! awk 'BEGIN{RS=";"} {gsub(/^[ \n]+/, ""); if ($0 != "" && $0 !~ /^(SELECT|SET SESSION TRANSACTION READ ONLY|SET SESSION max_execution_time = [0-9]+|SET time_zone = .\+00:00.)/) print "BAD: " $0}' <<<"$sql" | grep -q .
check "every mysql statement is a SELECT or a session SET" $?
! grep -qiE '\b(insert|update|delete|drop|alter|create|replace|grant|truncate)\b' <<<"$sql"
check "no SQL write keyword" $?
# shellcheck disable=SC2016
grep -qF 'MYSQL_PWD="$MYSQL_PASSWORD"' "$calls" && ! grep -E 'mysql .* -p' "$calls" | grep -q .
check "mysql password only from the pod's env" $?

# Kill switch on, and a flag value the api ignores.
KILL_EXISTS=1 KILL_VALUE=1
out=$(budget_section 2>&1)
grep -qF 'kill switch glassbox:kill:disable_llm: ON: LLM disabled' <<<"$out"
check "kill switch on" $?
KILL_EXISTS=1 KILL_VALUE=yes
out=$(budget_section 2>&1)
grep -qF 'flag present but not "1" (ignored by the api: off)' <<<"$out"
check "kill switch flag ignored unless 1" $?

# An unreadable ConfigMap falls back to the defaults, labelled, never to 0.
KILL_EXISTS=0 KILL_VALUE='' CONFIGMAP_OK=0
out=$(budget_section 2>&1)
grep -qF 'cap: default 100 (configmap unreadable) answers/day (= 400 quarter-units' <<<"$out"
check "unreadable cap falls back to the labelled default" $?
grep -qF 'used 117 of 400 units, 283 left' <<<"$out"
check "unreadable cap still computes against 100, not 0" $?
grep -qF '(cap default 10 (configmap unreadable)/day' <<<"$out"
check "unreadable warm cap falls back to the labelled default" $?

# More than 200 buckets: capped, and the count says so.
BUCKETS=201
out=$(budget_section 2>&1)
grep -qF 'rate-limit buckets active in the last 10 min: 200+ (200 shown)' <<<"$out"
check "bucket scan cap is reported" $?
BUCKETS=

# CSP Report-Only section: counts and directive/origin pairs only.
out=$(csp_section 2>&1)
printf '%s\n' "$out" | sed 's/^/     | /'
grep -qE '^violation lines +5$' <<<"$out"
check "csp: violation lines counted" $?
grep -qE '^oversize reports dropped +1$' <<<"$out"
check "csp: oversize lines counted" $?
grep -qE '^log-cap summary lines +1$' <<<"$out"
check "csp: log-cap lines counted" $?
grep -qE '^ +2 style-src-attr inline$' <<<"$out"
check "csp: pairs counted, most frequent first" $?
grep -qE '^ +1 connect-src https://cdn.example.com$' <<<"$out" && grep -qE '^ +1 img-src chrome-extension:$' <<<"$out"
check "csp: origins and schemes kept" $?
! grep -qE 'token|ABCDEFGH|POST|10\.42' <<<"$out"
check "csp: off-shape lines and access log never printed" $?
grep -qF 'logs deploy/api -c api --since=24h' "$calls"
check "csp: reads only the api container's last 24h" $?

# redis_read refuses anything that could write.
kc() { echo "SENT"; }
out=$(printf 'GET a\nDEL budget:llm:2026-10-01\n' | redis_read 2>&1)
rc=$?
[ "$rc" -ne 0 ] && ! grep -qF SENT <<<"$out"
check "redis_read refuses a write command" $?
out=$(printf 'GET a\nTTL b\n' | redis_read 2>&1)
grep -qF SENT <<<"$out"
check "redis_read sends read commands" $?

# ---------------------------------------------------------------------------
# The query-log SQL (ingest run summary, TTFT p50/p95) against a real MySQL 8.
# Runs only when docker is available; the table definitions mirror the columns
# the SQL reads (services/glassbox/db/models.py). Needs the mysql:8.0 image;
# the container publishes no port and is removed on exit. Skip with
# DIAGNOSE_TEST_SKIP_MYSQL=1.
if [ -n "${DIAGNOSE_TEST_SKIP_MYSQL:-}" ] || ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "skip real-MySQL checks (no docker, or DIAGNOSE_TEST_SKIP_MYSQL set)"
  exit "$fail"
fi
cid=$(docker run -d --rm -e MYSQL_ROOT_PASSWORD=rootpw -e MYSQL_USER=app -e MYSQL_PASSWORD=apppw \
  -e MYSQL_DATABASE=glassbox mysql:8.0)
trap 'docker rm -f "$cid" >/dev/null 2>&1; rm -rf "$work"' EXIT
for _ in $(seq 1 90); do
  docker exec "$cid" mysql -uroot -prootpw -e 'SELECT 1' >/dev/null 2>&1 && break
  sleep 2
done
sqlroot() { docker exec -i "$cid" mysql -uroot -prootpw glassbox 2>/dev/null; }
sqlroot <<'SQL'
CREATE TABLE queries (id BIGINT AUTO_INCREMENT PRIMARY KEY, corpus VARCHAR(20) NOT NULL DEFAULT 'about_me',
  question VARCHAR(1000) NOT NULL DEFAULT 'SECRET QUESTION TEXT', cache_status ENUM('answer_hit','miss') NOT NULL,
  mode ENUM('full','retrieval_only','stopped') NOT NULL DEFAULT 'full', turn_index INT NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, ttft_ms INT NULL);
CREATE TABLE ingestion_runs (id BIGINT AUTO_INCREMENT PRIMARY KEY, commit_sha CHAR(40), started_at TIMESTAMP NOT NULL,
  finished_at TIMESTAMP NULL, docs_changed INT DEFAULT 0, chunks_written INT DEFAULT 0,
  status ENUM('running','succeeded','failed') NOT NULL, notes JSON);
INSERT INTO queries (cache_status, ttft_ms) VALUES
  ('miss',100),('miss',200),('miss',300),('miss',400),('miss',500),('miss',600),('miss',700),('miss',800),('miss',900),('miss',1000),
  ('answer_hit',10),('answer_hit',20),('answer_hit',30),('miss',NULL);
INSERT INTO queries (cache_status, ttft_ms, created_at) VALUES ('miss', 99999, NOW() - INTERVAL 2 DAY);
INSERT INTO ingestion_runs (started_at, status, docs_changed, chunks_written, notes) VALUES
  (NOW() - INTERVAL 5 DAY, 'succeeded', 1, 1, '{"sweep":{"mode":"report","corpora":[]}}'),
  (NOW() - INTERVAL 90 MINUTE, 'succeeded', 4, 37, JSON_OBJECT('sweep', JSON_OBJECT('mode', 'apply', 'corpora', JSON_ARRAY(
    JSON_OBJECT('corpus','about_me','model','m','known',20,'planned',2,'deleted',2,'refused',NULL),
    JSON_OBJECT('corpus','portfolio','model','m','known',9,'planned',7,'deleted',0,'refused','would delete 77% (token=ABCDEFGHIJKLMNOPQRSTUVWXYZ)')))));
SQL
# Run the script's own mysql command inside the container instead of kubectl.
kc() {
  local a=("$@") i
  case "$*" in *"exec -i mysql-0"*) ;; *) return 0 ;; esac
  for i in "${!a[@]}"; do
    [ "${a[$i]}" = "--" ] && { docker exec -i "$cid" "${a[@]:$((i + 1))}"; return; }
  done
}
sqlout() { budget_section 2>&1; }
out=$(sqlout)
printf '%s\n' "$out" | sed 's/^/     | /'
grep -qF 'last ingest run #2: succeeded, started 90 min ago, docs changed 4, chunks written 37, sweep mode apply, corpora in sweep 2' <<<"$out"
check "sql: latest ingest run summary (not the older run)" $?
grep -qF '    sweep about_me (model m): known 20, planned 2, deleted 2, not refused' <<<"$out"
check "sql: per-corpus sweep counts" $?
grep -qF '    sweep portfolio (model m): known 9, planned 7, deleted 0, REFUSED (reason in ingestion_runs.notes)' <<<"$out"
check "sql: refusal flagged without printing its text" $?
! grep -qE 'ABCDEFGH|would delete|SECRET QUESTION' <<<"$out"
check "sql: no refusal or question text printed" $?
grep -qF 'ttft last 24h, cache_status answer_hit: n 3, p50 20 ms, p95 30 ms' <<<"$out"
check "sql: ttft nearest-rank p50/p95, cache hits" $?
grep -qF 'ttft last 24h, cache_status miss: n 10, p50 500 ms, p95 1000 ms' <<<"$out"
check "sql: ttft nearest-rank p50/p95, misses (NULL and 2-day-old rows ignored)" $?
# The SQL is run through the same read-only session as production.
out=$( { query_log_sql; echo "INSERT INTO queries (cache_status) VALUES ('miss');"; } | docker exec -i "$cid" mysql -uroot -prootpw --force -N -B glassbox 2>&1 || true)
grep -qiE 'read.only' <<<"$out"
check "sql: session is read-only (an INSERT after it is refused)" $?

export cid
export -f kc
sub=$(bash -c "$(declare -f $BUDGET_FUNCTIONS); budget_section" 2>&1)
grep -qF 'last ingest run #2:' <<<"$sub" && grep -qF 'ttft last 24h, cache_status miss: n 10' <<<"$sub" && grep -qF 'asks: ' <<<"$sub"
check "subshell (as main runs it): ingest, ttft and query-log lines appear" $?

# Degrade: no ingestion runs and no ttft values.
sqlroot <<'SQL'
DELETE FROM ingestion_runs; UPDATE queries SET ttft_ms = NULL;
SQL
out=$(sqlout)
grep -qF 'last ingest run: none recorded' <<<"$out"
check "sql: no ingestion runs" $?
grep -qF 'ttft last 24h: no asks with a ttft_ms' <<<"$out"
check "sql: all ttft_ms NULL" $?
grep -qF 'asks: ' <<<"$out"
check "sql: earlier statements still printed" $?
# A run with NULL notes.
sqlroot <<'SQL'
INSERT INTO ingestion_runs (started_at, status) VALUES (NOW(), 'failed');
SQL
out=$(sqlout)
grep -qF 'last ingest run #3: failed, started 0 min ago, docs changed 0, chunks written 0, sweep mode none, corpora in sweep 0' <<<"$out"
check "sql: NULL notes" $?
# Degrade: missing column / missing table must not hide the rest.
sqlroot <<'SQL'
ALTER TABLE queries DROP COLUMN ttft_ms; DROP TABLE ingestion_runs;
SQL
out=$(sqlout)
grep -qF 'asks: ' <<<"$out" && grep -qF 'by mode/cache' <<<"$out"
check "sql: missing table/column leaves the other statements running" $?
! grep -qE 'apppw|rootpw' <<<"$out"
check "sql: no password in output" $?

exit "$fail"
