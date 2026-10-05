#!/usr/bin/env bash
# Offline test for the restore-snapshot and list-snapshots paths of
# .github/scripts/ops-run.sh, with stubbed aws and sleep. Bad input and every
# failed precondition must stop the run before EC2 is asked to replace the
# root volume (fail closed); "latest" must pick the newest completed daily
# snapshot of this instance. Needs jq. No AWS.
#   bash infra/modules/ops/tests/restore-snapshot-test.sh
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
# Every call is logged. STUB_* variables choose the answers.
cat >"$work/bin/aws" <<'S'
#!/bin/sh
echo "$*" >>"$STUB_LOG"
case "$*" in
  *describe-instances*State.Name*) echo "${STUB_STATE:-running}" ;;
  *describe-instances*BlockDeviceMappings*) echo vol-0aaaaaaaaaaaaaaaa ;;
  *describe-instances*) echo i-0123456789abcdef0 ;;
  *describe-replace-root-volume-tasks*--replace-root-volume-task-ids*) echo "${STUB_TASK_STATE:-succeeded}" ;;
  *describe-replace-root-volume-tasks*--output\ json*)
    [ -n "${STUB_TASKS_FAIL:-}" ] && exit 254
    if [ -n "${STUB_TASKS:-}" ]; then echo "$STUB_TASKS"; else echo '{"ReplaceRootVolumeTasks": []}'; fi ;;
  *describe-replace-root-volume-tasks*) echo "replacevol-0old succeeded" ;;
  *describe-snapshots*)
    [ -n "${STUB_SNAPS_FAIL:-}" ] && exit 254
    cat "$STUB_SNAPS" ;;
  *create-replace-root-volume-task*) echo replacevol-0123456789abcdef0 ;;
  *describe-alarms*) echo "glassbox-node-reboot OK 2026-10-01T00:00:00Z True" ;;
  *disable-alarm-actions*) [ -z "${STUB_DISABLE_FAIL:-}" ] || exit 254 ;;
  *enable-alarm-actions*) [ -z "${STUB_ENABLE_FAIL:-}" ] || exit 254 ;;
  *describe-volumes*)
    [ -n "${STUB_VOLS_FAIL:-}" ] && exit 254
    echo '{"Volumes": [{"VolumeId": "vol-0bbbbbbbbbbbbbbbb", "CreateTime": "2026-09-01T00:00:00Z", "Size": 20, "VolumeType": "gp3"}]}' ;;
  *list-attached-role-policies*)
    [ -n "${STUB_IAM_FAIL:-}" ] && exit 254
    echo "${STUB_STOP_ATTACHED:-}" ;;
  *send-command*) echo cmd-1 ;;
  *get-command-invocation*) cat "$STUB_INVOCATION" ;;
esac
S
chmod +x "$work/bin/sleep" "$work/bin/aws"

fail=0
check() { # check <label> <condition-exit-status>
  if [ "$2" -eq 0 ]; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi
}

export PATH="$work/bin:$PATH" STUB_LOG="$work/log" STUB_SNAPS="$work/snaps.json"
export STUB_INVOCATION="$work/inv.json" GITHUB_STEP_SUMMARY="$work/summary" AWS_REGION=us-east-1
jq -n '{Status: "Success", StandardOutputContent: "boot_id=new-boot\nboot_epoch=1\n", StandardErrorContent: ""}' >"$STUB_INVOCATION"

# Three daily snapshots of this instance (the newest still pending), and one
# of another instance that must never be chosen.
snap() { # snap <id> <start> <state> <instance>
  jq -n --arg id "$1" --arg t "$2" --arg s "$3" --arg i "$4" \
    '{SnapshotId: $id, StartTime: $t, State: $s, Progress: "100%", VolumeSize: 20,
      Tags: [{Key: "glassbox-backup", Value: "daily-root"}, {Key: "instance-id", Value: $i}]}'
}
{
  snap snap-0000000000000000a 2026-09-29T04:10:00Z completed i-0123456789abcdef0
  snap snap-0000000000000000c 2026-10-01T04:10:00Z pending i-0123456789abcdef0
  snap snap-0000000000000000b 2026-09-30T04:10:00Z completed i-0123456789abcdef0
  snap snap-0000000000000000f 2026-10-01T05:00:00Z completed i-0fffffffffffffff0
} | jq -s '{Snapshots: .}' >"$STUB_SNAPS"

run() { # run <env assignments...>: restore with a clean log; output in $out, status in $rc
  : >"$STUB_LOG"
  : >"$GITHUB_STEP_SUMMARY"
  out=$(env "$@" bash "$script" act restore-snapshot 2>&1)
  rc=$?
}
aws_calls() { [ -s "$STUB_LOG" ] && echo 1 || echo 0; } # 0 = none
created() { grep -q create-replace-root-volume-task "$STUB_LOG"; }
# order_ok <first> <second>: both logged, first before second.
order_ok() {
  local a b
  a=$(grep -n -- "$1" "$STUB_LOG" | head -1 | cut -d: -f1)
  b=$(grep -n -- "$2" "$STUB_LOG" | tail -1 | cut -d: -f1)
  [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]
}
refused() { # refused <label> <expected message>
  [ "$rc" -ne 0 ]
  check "$1: exits non-zero" $?
  ! created
  check "$1: no replacement requested" $?
  grep -qF "$2" <<<"$out"
  check "$1: says why" $?
  ! grep -q alarm-actions "$STUB_LOG"
  check "$1: reboot alarm never paused" $?
}

# 1. Input validation happens before any AWS call.
for bad in '' 'snap-xyz' 'snap-0123; rm -rf /' 'LATEST' 'snap-0000000000000000a ' 'vol-0aaaaaaaaaaaaaaaa'; do
  run SNAPSHOT="$bad" OLD_VOLUME=keep
  refused "snapshot '$bad'" "must be 'latest' or a snapshot ID"
  check "snapshot '$bad': no AWS call at all" "$(aws_calls)"
done
for bad in '' yes KEEP 'keep;'; do
  run SNAPSHOT=latest OLD_VOLUME="$bad"
  refused "old_volume '$bad'" "is not one of: keep delete"
  check "old_volume '$bad': no AWS call at all" "$(aws_calls)"
done

# 2. Preconditions.
run SNAPSHOT=latest OLD_VOLUME=keep STUB_STATE=stopped
refused "stopped instance" "the instance is 'stopped'"
run SNAPSHOT=latest OLD_VOLUME=keep STUB_TASKS='{"ReplaceRootVolumeTasks": [{"TaskState": "in-progress"}]}'
refused "restore already in flight" "already in progress"
run SNAPSHOT=latest OLD_VOLUME=keep STUB_TASKS_FAIL=1
refused "task list unreadable" "could not check for restores already in progress"
run SNAPSHOT=latest OLD_VOLUME=keep STUB_SNAPS_FAIL=1
refused "snapshot list unreadable" "could not list the daily snapshots"
run SNAPSHOT=snap-0000000000000000f OLD_VOLUME=keep
refused "another instance's snapshot" "is not one of this instance's daily snapshots"
run SNAPSHOT=snap-0123456789abcdef9 OLD_VOLUME=keep
refused "unknown snapshot" "is not one of this instance's daily snapshots"
run SNAPSHOT=snap-0000000000000000c OLD_VOLUME=keep
refused "pending snapshot" "is still pending"
echo '{"Snapshots": []}' >"$work/empty.json"
run SNAPSHOT=latest OLD_VOLUME=keep STUB_SNAPS="$work/empty.json"
refused "no snapshots yet" "no completed daily snapshot"

# 3. latest = newest completed snapshot of this instance; old volume kept.
run SNAPSHOT=latest OLD_VOLUME=keep
check "latest: succeeds" "$rc"
grep create-replace-root-volume-task "$STUB_LOG" | grep -q -- '--snapshot-id snap-0000000000000000b'
check "latest: newest completed snapshot of this instance (b)" $?
grep create-replace-root-volume-task "$STUB_LOG" | grep -q -- '--instance-id i-0123456789abcdef0'
check "latest: on the glassbox instance" $?
! grep -q -- '--delete-replaced-root-volume' "$STUB_LOG"
check "keep: old root volume not deleted" $?
grep -qF 'kept, detached' "$GITHUB_STEP_SUMMARY"
check "keep: summary says the old volume is kept" $?
order_ok 'disable-alarm-actions --region us-east-1 --alarm-names glassbox-node-reboot' create-replace-root-volume-task
check "reboot alarm paused before the replacement" $?
order_ok create-replace-root-volume-task 'enable-alarm-actions --region us-east-1 --alarm-names glassbox-node-reboot'
check "reboot alarm re-enabled after the replacement" $?

# 4. An explicit ID, with the old volume deleted.
run SNAPSHOT=snap-0000000000000000a OLD_VOLUME=delete
check "explicit id: succeeds" "$rc"
grep create-replace-root-volume-task "$STUB_LOG" | grep -q -- '--snapshot-id snap-0000000000000000a --delete-replaced-root-volume'
check "delete: passes --delete-replaced-root-volume" $?

# 5. A failed replacement fails the run.
run SNAPSHOT=latest OLD_VOLUME=keep STUB_TASK_STATE=failed
[ "$rc" -ne 0 ] && grep -qF 'kept the original root volume' <<<"$out"
check "failed task: exits non-zero and says the original volume stayed" $?
order_ok disable-alarm-actions enable-alarm-actions
check "failed task: reboot alarm re-enabled on the way out" $?
run SNAPSHOT=latest OLD_VOLUME=keep STUB_TASK_STATE=failed-detached
[ "$rc" -ne 0 ] && grep -qF 'NO root volume' <<<"$out"
check "failed-detached task: exits non-zero and warns" $?

# 5b. Alarm pause problems: can't pause -> restore anyway; can't re-enable ->
# the run fails and says the alarm is still paused.
run SNAPSHOT=latest OLD_VOLUME=keep STUB_DISABLE_FAIL=1
check "pause fails: restore still succeeds" "$rc"
! grep -q enable-alarm-actions <(grep -v disable-alarm-actions "$STUB_LOG")
check "pause fails: nothing to re-enable" $?
run SNAPSHOT=latest OLD_VOLUME=keep STUB_ENABLE_FAIL=1
[ "$rc" -ne 0 ] && grep -qF 'still paused' <<<"$out"
check "re-enable fails: exits non-zero and says the alarm is still paused" $?

# 6. list-snapshots: IDs and dates of this instance's snapshots only; fails
# soft (a note, exit 0) while the role can't list them yet.
: >"$GITHUB_STEP_SUMMARY"
out=$(bash "$script" snapshots 2>&1)
rc=$?
check "list: succeeds" "$rc"
grep -qF 'snap-0000000000000000b  2026-09-30T04:10:00Z  completed' <<<"$out"
check "list: shows id, date and state" $?
! grep -qF snap-0000000000000000f <<<"$out"
check "list: hides other instances' snapshots" $?
grep -qF 'glassbox-node-reboot OK' "$GITHUB_STEP_SUMMARY"
check "list: alarm states in the summary" $?
grep -qF "vol-0bbbbbbbbbbbbbbbb  2026-09-01T00:00:00Z  20 GiB gp3  about \$1.6/month" <<<"$out"
check "list: shows detached volumes with a cost hint" $?
out=$(STUB_SNAPS_FAIL=1 STUB_VOLS_FAIL=1 bash "$script" snapshots 2>&1)
rc=$?
check "list without permission: still exits 0" "$rc"
grep -qF 'could not list snapshots' <<<"$out" && grep -qF 'could not list volumes' <<<"$out"
check "list without permission: says so" $?

# 7. The budget stop line: off, on, and unreadable (before the Bootstrap run).
out=$(bash "$script" snapshots 2>&1)
grep -qF 'off (answers enabled)' <<<"$out"
check "budget stop: off when the deny policy is not attached" $?
out=$(STUB_STOP_ATTACHED=glassbox-budget-stop-answer-models bash "$script" snapshots 2>&1)
grep -qF 'ON: glassbox-budget-stop-answer-models is attached to glassbox-instance' <<<"$out"
check "budget stop: ON when the deny policy is attached" $?
grep -q 'list-attached-role-policies --role-name glassbox-instance' "$STUB_LOG"
check "budget stop: reads only the node's role" $?
out=$(STUB_IAM_FAIL=1 bash "$script" snapshots 2>&1)
rc=$?
check "budget stop unreadable: still exits 0" "$rc"
grep -qF "can't list the node role's policies" <<<"$out"
check "budget stop unreadable: says so" $?

exit "$fail"
