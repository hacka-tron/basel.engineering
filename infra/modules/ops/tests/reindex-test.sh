#!/usr/bin/env bash
# Offline test for ../scripts/reindex.sh (glassbox-ops-reindex) with a stubbed
# kubectl: it refuses while an ingest or reindex Job runs or the api is
# mid-rollout, uses the api's image, never reads a Secret, reports a failed
# Job as a failure, and its Job keeps the names the ingest Job uses
# (k8s/overlays/prod/ingest/ingest-job.yaml). Needs no cluster or AWS.
#   bash infra/modules/ops/tests/reindex-test.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
ingest_job=$here/../../../../k8s/overlays/prod/ingest/ingest-job.yaml
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# reindex.sh ends by calling main; load its functions without running it.
grep -vxF 'main "$@"' "$here/../scripts/reindex.sh" >"$work/reindex-functions.sh"
calls=$work/calls
created=$work/created

fail=0
check() { # check <label> <condition-exit-status>
  if [ "$2" -eq 0 ]; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi
}

IMAGE=123456789012.dkr.ecr.us-east-1.amazonaws.com/glassbox:build-42

# run_main: runs main in a subshell with the stubs below; prints its output.
# Scenario variables: JOBS, ROLLOUT, API_IMAGE, JOB_STATE.
run_main() {
  (
    # shellcheck source=../scripts/lib.sh
    source "$here/../scripts/lib.sh"
    # shellcheck source=../scripts/reindex.sh
    source "$work/reindex-functions.sh"
    sleep() { SECONDS=$((SECONDS + ${1:-0})); }
    kc() {
      printf '%s\n' "$*" >>"$calls"
      case "$*" in
        *"get jobs -o jsonpath"*) printf '%b' "$JOBS" ;;
        *"get deployment api -o jsonpath={.metadata.generation}"*) echo "$ROLLOUT" ;;
        *"get deployment api -o jsonpath={.spec.template"*) echo "$API_IMAGE" ;;
        "create --field-manager=glassbox-ops -f -") cat >"$created" ;;
        *"get job ops-reindex-"*"-o jsonpath=complete="*)
          case "$JOB_STATE" in
            ok) echo "complete=True failed=" ;;
            failed) echo "complete= failed=True" ;;
            *) echo "complete= failed=" ;;
          esac
          ;;
        *"logs job/ops-reindex-"*) echo "reconcile about_me: repaired=0 rewritten=12 removed=0 password=hunter2" ;; # pragma: allowlist secret
        *) : ;;
      esac
    }
    main
  ) 2>&1
}

reset() {
  : >"$calls"
  rm -f "$created"
  JOBS='ingest \nwarm-answers-29000 1\n'
  ROLLOUT='3|3|1|1|1'
  API_IMAGE=$IMAGE
  JOB_STATE=ok
}

# 1. Happy path: the Job uses the api's image, runs --reindex, and succeeds.
reset
out=$(run_main)
rc=$?
check "succeeds when the Job completes" "$rc"
grep -qF "image: $IMAGE" "$created"
check "Job uses the api Deployment's image" $?
grep -qF '"--reindex"' "$created" && grep -qF 'services.glassbox.ingest.run' "$created"
check "Job runs ingest.run --reindex" $?
grep -qE '^  name: ops-reindex-[0-9]{14}$' "$created"
check "Job is named ops-reindex-<timestamp>" $?
grep -qF 'backoffLimit: 0' "$created" && grep -qF 'restartPolicy: Never' "$created"
check "Job never retries" $?
! grep -qF hunter2 <<<"$out" && grep -qF 'rewritten=12' <<<"$out"
check "Job log is printed, redacted" $?
check "no Secret is read" "$(grep -ciE 'get secrets?( |$)|secret/' "$calls")"

# 2. The Job's config names match the ingest Job's.
for name in 'name: glassbox-config' 'name: glassbox-mysql' 'key: mysql-password' \
  'name: MYSQL_PASSWORD' 'name: regcred' 'app: ingest' 'namespace: app'; do
  grep -qF "$name" "$created" && grep -qF "$name" "$ingest_job"
  check "'$name' in both the reindex Job and the ingest Job" $?
done

# 3. Refusals: nothing is created.
refuses() { # refuses <label> <expected message part>
  local out rc
  out=$(run_main)
  rc=$?
  [ "$rc" -ne 0 ] && [ ! -e "$created" ] && grep -qF "$2" <<<"$out"
  check "refuses: $1" $?
}
reset
JOBS='ingest 1\n'
refuses "release ingest Job running" "job/ingest is still running"
reset
JOBS='ingest \nops-reindex-20261002000000 1\n'
refuses "earlier reindex still running" "job/ops-reindex-20261002000000 is still running"
reset
ROLLOUT='4|3|1|1|1'
refuses "api rollout not observed yet" "mid-rollout"
reset
ROLLOUT='3|3|1|1|'
refuses "api not ready" "mid-rollout"
reset
ROLLOUT='3|3|1||1'
refuses "api new pods not rolled out (missing field doesn't shift)" "mid-rollout"
reset
API_IMAGE='evil.example.com/glassbox:build-1'
refuses "unexpected image" "unexpected api image"

# 4. A failed or unfinished Job fails the run, after printing its log.
reset
JOB_STATE=failed
out=$(run_main)
rc=$?
[ "$rc" -ne 0 ] && grep -qF 'the reindex Job failed' <<<"$out" && grep -qF 'rewritten=12' <<<"$out"
check "failed Job: non-zero, says so, prints the log" $?
reset
JOB_STATE=running
out=$(run_main)
rc=$?
[ "$rc" -ne 0 ] && grep -qF 'did not finish' <<<"$out"
check "unfinished Job: non-zero after the wait" $?

exit "$fail"
