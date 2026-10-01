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
grep -qF 'about_me 3, about_system 41' <<<"$out"
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

# Read-only: no Secret reads, and Redis only gets GET/TTL/EXISTS/HGET/SCAN.
! grep -qiE 'secret' "$calls"
check "no Secret read" $?
grep -q '^  stdin: ' "$calls" &&
  ! sed -n 's/^  stdin: //p' "$calls" |
  grep -vE '^(GET|TTL|EXISTS|HGET) [A-Za-z0-9:_.-]+( tokens)?$|^SET SESSION (TRANSACTION READ ONLY|max_execution_time = [0-9]+);$|^SET time_zone = .\+00:00.;$|^SELECT CONCAT\(|^  FROM queries WHERE ' |
  grep -q .
check "redis and mysql get only read commands" $?
! grep -qiE '(insert|update|delete|drop|alter|create|replace|grant) ' <(sed -n 's/^  stdin: //p' "$calls" | grep -v '^GET\|^TTL\|^EXISTS\|^HGET')
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

exit "$fail"
