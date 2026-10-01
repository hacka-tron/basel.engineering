#!/usr/bin/env bash
# Offline test for the "LLM budget" section of ../scripts/diagnose.sh, with a
# stubbed kubectl. Checks the numbers it prints, that Redis is only asked
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
# Stub kubectl: logs every call (and any stdin) and answers like the cluster.
kc() {
  local input="" all="$*"
  if [[ $all == *"exec -i"* ]]; then input=$(cat); fi
  printf '%s\n' "$all" >>"$calls"
  [ -z "$input" ] || printf '  stdin: %s\n' "${input//$'\n'/$'\n'  stdin: }" >>"$calls"
  case "$*" in
    *GLASSBOX_DAILY_LLM_CAP*) echo 100 ;;
    *GLASSBOX_WARM_DAILY_LLM_CAP*) echo 10 ;;
    *"--scan --pattern rl:*"*)
      echo "rl:$(printf 'a%.0s' {1..64})"
      echo "rl:$(printf 'b%.0s' {1..64})"
      echo "rl:not-a-hash"
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
          *) echo "UNEXPECTED $line" ;;
        esac
      done <<<"$input"
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

grep -qF 'configured cap: 100 answers/day (= 400 quarter-units' <<<"$out"
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
check "bucket count ignores malformed names" $?
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
