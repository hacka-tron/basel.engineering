#!/usr/bin/env bash
# Offline test for .github/scripts/ops-run.sh with stubbed aws and sleep:
# SSM output must go through redact() before it reaches the log or the step
# summary, the reason input is redacted, and a reboot with no pre-reboot boot
# ID must not report success. Needs jq. No AWS or cluster.
#   bash infra/modules/ops/tests/ops-run-test.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
script=$here/../../../../.github/scripts/ops-run.sh
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir "$work/bin"

cat >"$work/bin/sleep" <<'S'
#!/bin/sh
exit 0
S
cat >"$work/bin/aws" <<'S'
#!/bin/sh
case "$*" in
  *describe-instances*) echo i-0123456789abcdef0 ;;
  *send-command*) echo cmd-1 ;;
  *get-command-invocation*)
    if [ -n "${STUB_NO_ANSWER:-}" ]; then exit 255; fi
    cat "$STUB_INVOCATION"
    ;;
  *reboot-instances*) echo "STUB reboot" >>"$STUB_LOG" ;;
esac
S
chmod +x "$work/bin/sleep" "$work/bin/aws"

fail=0
check() { # check <label> <condition-exit-status>
  if [ "$2" -eq 0 ]; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi
}

export PATH="$work/bin:$PATH" STUB_LOG="$work/log" STUB_INVOCATION="$work/inv.json"
export GITHUB_STEP_SUMMARY="$work/summary" AWS_REGION=us-east-1

# 1. Output and stderr are redacted in the log and in the summary.
jq -n '{Status: "Success",
  StandardOutputContent: "login password=s3cr3tpw ok\nclone https://deploy:hunter2@github.com/o/r\nip-10-0-1-5 Ready", # pragma: allowlist secret
  StandardErrorContent: "Authorization: Bearer BEARERVAL"}' >"$STUB_INVOCATION" # pragma: allowlist secret
: >"$GITHUB_STEP_SUMMARY"
out=$(bash "$script" diagnose t 2>&1)
rc=$?
check "diagnose succeeds" "$rc"
for secret in s3cr3tpw hunter2 BEARERVAL; do
  ! grep -qF "$secret" <<<"$out" && ! grep -qF "$secret" "$GITHUB_STEP_SUMMARY"
  check "'$secret' absent from log and summary" $?
done
grep -qF 'ip-10-0-1-5 Ready' <<<"$out"
check "harmless output survives" $?

# 2. The reason input is redacted by the same function.
r=$(printf '%s\n%s' 'rotate token=tok123 now' 'line two' | bash "$script" redact)
! grep -qF tok123 <<<"$r" && grep -qF 'line two' <<<"$r"
check "reason redacted, newlines flattened" $?

# 3. No pre-reboot boot ID: the reboot is issued, but the run must fail and
# say it could not prove a restart (even though the node later answers with a
# boot time that looks fresh).
jq -n '{Status: "Success", StandardOutputContent: "boot_id=aaaa-bbbb\nboot_epoch=99999999999\n", StandardErrorContent: ""}' >"$STUB_INVOCATION"
: >"$GITHUB_STEP_SUMMARY"
: >"$STUB_LOG"
out=$(STUB_NO_ANSWER=1 bash "$script" act reboot-node 2>&1)
rc=$?
[ "$rc" -ne 0 ]
check "reboot without pre-reboot boot ID exits non-zero" $?
grep -qF 'could not prove the node restarted (no pre-reboot boot ID)' <<<"$out"
check "reboot without pre-reboot boot ID says why" $?
grep -qF 'STUB reboot' "$STUB_LOG"
check "reboot was still issued" $?

exit "$fail"
