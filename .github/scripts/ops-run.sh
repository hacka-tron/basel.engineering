#!/usr/bin/env bash
# Runs one runbook action for .github/workflows/ops.yml. Not for local use.
#
#   ops-run.sh diagnose [title]
#   ops-run.sh snapshots   (read-only: alarms, daily snapshots, restore tasks)
#   ops-run.sh act <action>
#   ops-run.sh redact      (stdin to stdout through redact(); for free text)
#
# Inputs come from environment variables set by the workflow (never
# interpolated into this script): DEPLOYMENT, FLUX_TARGET, KEDA_REPLICAS,
# CRONJOB, SNAPSHOT, OLD_VOLUME. Each is checked against a fixed list or
# pattern here, before any AWS call; SSM checks the document parameters
# again against their allowedValues.
#
# Everything on the node runs through Terraform-managed SSM documents
# (infra/modules/ops, and glassbox-zram-swap in infra/modules/compute); the
# assumed role can't send any other document. restore-snapshot is an EC2 API
# call (replace root volume), not a command on the node.
set -euo pipefail

# Single shared redact() (the one the on-node scripts use). lib.sh also sets
# PATH/KUBECONFIG and its own log/die; keep the runner's PATH and define this
# script's log/die below, after the source.
runner_path=$PATH
# shellcheck source=../../infra/modules/ops/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../../infra/modules/ops/scripts/lib.sh"
PATH=$runner_path
unset KUBECONFIG

# Everything printed to the public log or the step summary goes through
# redact (SSM output, even though the on-node scripts already redact their own
# free text: this is the second, independent pass). Width is generous because
# the on-node scripts already truncated their lines.
REDACT_WIDTH=400

REGION=${AWS_REGION:-us-east-1}
SUMMARY=${GITHUB_STEP_SUMMARY:-/dev/null}

log() { printf '[ops %s] %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
die() {
  echo "::error::$*"
  echo "**Failed:** $*" >>"$SUMMARY"
  exit 1
}

require_one_of() {
  local name=$1 value=$2 allowed
  shift 2
  for allowed in "$@"; do
    [ "$value" = "$allowed" ] && return 0
  done
  die "input $name='$value' is not one of: $*"
}

instance_id() {
  local ids
  ids=$(aws ec2 describe-instances --region "$REGION" \
    --filters Name=tag:Name,Values=glassbox Name=tag:project,Values=glassbox \
    Name=instance-state-name,Values=pending,running,stopping,stopped \
    --query 'Reservations[].Instances[].InstanceId' --output text)
  [ "$(wc -w <<<"$ids")" -eq 1 ] || die "expected exactly one glassbox instance, found: '${ids}'"
  echo "$ids"
}

# send_and_wait <document> <parameters-json> <max-wait-seconds> <title>
# Prints the command's output and returns its success. The output is also left
# in LAST_STDOUT. With QUIET=1 nothing is printed or added to the summary
# (used for the tiny boot-id probes).
LAST_STDOUT=''
send_and_wait() {
  local document=$1 parameters=$2 max_wait=$3 title=$4
  local instance command_id deadline status invocation result stdout stderr raw_len

  instance=$(instance_id)
  log "sending $document to $instance (parameters: $parameters)"
  # --timeout-seconds is the delivery timeout: how long SSM keeps trying to
  # reach an agent that is busy or offline (a swapping node can take
  # minutes). The script's own execution timeout is set in the document.
  command_id=$(aws ssm send-command --region "$REGION" \
    --instance-ids "$instance" \
    --document-name "$document" \
    --parameters "$parameters" \
    --timeout-seconds 600 \
    --comment "gh run ${GITHUB_RUN_ID:-local} by ${GITHUB_ACTOR:-unknown}: $title" \
    --query Command.CommandId --output text)
  log "command id $command_id; waiting up to ${max_wait}s"

  deadline=$((SECONDS + max_wait))
  status=Pending
  invocation='{}'
  while [ "$SECONDS" -lt "$deadline" ]; do
    sleep 10
    if ! result=$(aws ssm get-command-invocation --region "$REGION" \
      --command-id "$command_id" --instance-id "$instance" --output json 2>/dev/null); then
      continue # InvocationDoesNotExist until the command is registered
    fi
    invocation=$result
    status=$(jq -r .Status <<<"$invocation")
    case "$status" in
      Pending | InProgress | Delayed) continue ;;
      *) break ;;
    esac
  done

  stdout=$(jq -r '.StandardOutputContent // ""' <<<"$invocation")
  LAST_STDOUT=$stdout # raw, only parsed (boot-id probe), never printed
  if [ "${QUIET:-0}" = 1 ]; then
    [ "$status" = Success ]
    return
  fi
  raw_len=${#stdout}
  stdout=$(redact "$REDACT_WIDTH" <<<"$stdout")
  stderr=$(jq -r '.StandardErrorContent // ""' <<<"$invocation" | redact "$REDACT_WIDTH")
  echo "::group::$title output ($status)"
  printf '%s\n' "$stdout"
  [ -z "$stderr" ] || printf -- '--- stderr ---\n%s\n' "$stderr"
  echo "::endgroup::"
  if [ "$raw_len" -ge 23900 ]; then
    echo "::warning::SSM keeps only the first 24,000 characters of output; the end of the $title output may be cut off."
  fi
  {
    echo "<details><summary>$title: $status</summary>"
    echo
    echo '```'
    printf '%s\n' "$stdout" | tail -c 60000
    echo '```'
    echo "</details>"
    echo
  } >>"$SUMMARY"

  case "$status" in
    Success) return 0 ;;
    Pending | InProgress | Delayed)
      echo "::error::$title still $status after ${max_wait}s (command $command_id keeps running on the node; check it later with diagnose)"
      return 1
      ;;
    *)
      echo "::error::$title finished with status $status"
      return 1
      ;;
  esac
}

# The tags the daily DLM policy puts on its snapshots
# (infra/modules/compute/snapshots.tf). glassbox-ops may only restore from
# snapshots carrying them (infra/bootstrap/runbooks.tf).
SNAPSHOT_FILTERS=('Name=tag:glassbox-backup,Values=daily-root' 'Name=tag:project,Values=glassbox')

# daily_snapshots <instance-id>: JSON array of this instance's daily
# snapshots, oldest first: [{id, start, state, progress, size, instance}].
daily_snapshots() {
  aws ec2 describe-snapshots --region "$REGION" --owner-ids self \
    --filters "${SNAPSHOT_FILTERS[@]}" --output json |
    jq --arg i "$1" '[.Snapshots[]
      | {id: .SnapshotId, start: .StartTime, state: .State,
         progress: (.Progress // ""), size: .VolumeSize,
         instance: ((.Tags // []) | map(select(.Key == "instance-id")) | .[0].Value // "")}
      | select(.instance == $i)] | sort_by(.start)'
}

# The deny policy AWS Budgets attaches to the node's role at 100% of the
# monthly budget (infra/modules/compute/budget.tf). Attached means Bedrock
# answers are off and the site answers retrieval-only.
BUDGET_STOP_POLICY=glassbox-budget-stop-answer-models
NODE_ROLE=glassbox-instance

budget_stop_status() {
  local attached
  if ! attached=$(aws iam list-attached-role-policies --role-name "$NODE_ROLE" \
    --query "AttachedPolicies[?PolicyName=='$BUDGET_STOP_POLICY'].PolicyName" \
    --output text 2>/dev/null); then
    echo "(could not read: the ops roles can't list the node role's policies until the Bootstrap run)"
  elif [ -n "$attached" ] && [ "$attached" != "None" ]; then
    echo "ON: $BUDGET_STOP_POLICY is attached to $NODE_ROLE. Answers are retrieval-only until"
    echo "the 1st of next month (UTC) or until the action is reversed in the Budgets console."
  else
    echo "off (answers enabled)"
  fi
}

# Read-only: the alarms, the daily snapshots and recent root volume
# replacement tasks, from the AWS API (works even when the node is down).
# Each part fails soft: a missing permission (before the Bootstrap run) or
# a missing resource (before the Terraform apply) prints a note, not an
# error. Only IDs, dates, states and sizes are printed.
aws_overview() {
  local instance out
  instance=$(instance_id)
  out=$(
    echo "== status-check alarms (glassbox-node-*) =="
    echo "(name, state, since, actions enabled: False means paused, e.g. by an interrupted restore)"
    aws cloudwatch describe-alarms --region "$REGION" --alarm-name-prefix glassbox-node- \
      --query 'MetricAlarms[].[AlarmName,StateValue,StateUpdatedTimestamp,ActionsEnabled]' --output text 2>/dev/null ||
      echo "(could not read alarms: not applied yet, or the role lacks cloudwatch:DescribeAlarms)"
    echo
    echo "== AWS budget stop (glassbox-monthly-cost; infra/CI.md \"Budget stop\") =="
    budget_stop_status
    echo
    echo "== daily snapshots of $instance (newest last; restore with 'Ops · Restore from snapshot') =="
    if snaps=$(daily_snapshots "$instance" 2>/dev/null); then
      if jq -e 'length > 0' >/dev/null 2>&1 <<<"$snaps"; then
        jq -r '.[] | "\(.id)  \(.start)  \(.state) \(.progress)  \(.size) GiB"' <<<"$snaps"
      else
        echo "(none yet: the first is taken within an hour after 04:00 UTC once the policy is applied)"
      fi
    else
      echo "(could not list snapshots: the role lacks ec2:DescribeSnapshots until the Bootstrap run)"
    fi
    echo
    echo "== root volume replacement tasks (restores) =="
    aws ec2 describe-replace-root-volume-tasks --region "$REGION" \
      --filters "Name=instance-id,Values=$instance" \
      --query 'ReplaceRootVolumeTasks[].[ReplaceRootVolumeTaskId,TaskState,StartTime,CompleteTime]' \
      --output text 2>/dev/null || echo "(could not list restore tasks)"
    echo
    echo "== detached volumes (e.g. old root volumes kept by restores; they cost money until deleted) =="
    if vols=$(aws ec2 describe-volumes --region "$REGION" --filters Name=status,Values=available \
      --output json 2>/dev/null); then
      if jq -e '.Volumes | length > 0' >/dev/null <<<"$vols"; then
        jq -r '.Volumes[] | "\(.VolumeId)  \(.CreateTime)  \(.Size) GiB \(.VolumeType)  about $\(.Size * 0.08 * 100 | round / 100)/month"' <<<"$vols"
        echo "(delete them in the EC2 console once you no longer need them; the ops roles can't)"
      else
        echo "(none)"
      fi
    else
      echo "(could not list volumes: the role lacks ec2:DescribeVolumes until the Bootstrap run)"
    fi
  )
  out=$(redact "$REDACT_WIDTH" <<<"$out")
  echo "::group::alarms, snapshots and restores"
  printf '%s\n' "$out"
  echo "::endgroup::"
  {
    echo "<details><summary>Alarms, snapshots and restores</summary>"
    echo
    echo '```'
    printf '%s\n' "$out"
    echo '```'
    echo "</details>"
    echo
  } >>"$SUMMARY"
}

diagnose() {
  local title=${1:-diagnose} rc=0
  send_and_wait glassbox-ops-diagnose '{}' 600 "$title" || rc=$?
  # From the AWS API, so it still prints when the node can't answer.
  aws_overview || echo "::warning::could not list alarms and snapshots"
  return "$rc"
}

# boot_info <max-wait-seconds>: prints "<boot_id> <boot_epoch>" from the
# read-only glassbox-ops-boot-id document, or nothing if the node did not
# answer in time (a late answer is harmless: the document only reads).
boot_info() {
  local id epoch
  QUIET=1 send_and_wait glassbox-ops-boot-id '{}' "$1" boot-id >/dev/null || return 0
  id=$(sed -n 's/^boot_id=//p' <<<"$LAST_STDOUT" | head -1)
  epoch=$(sed -n 's/^boot_epoch=//p' <<<"$LAST_STDOUT" | head -1)
  [ -n "$id" ] && [ "$id" != unknown ] && echo "$id ${epoch:-0}"
  return 0
}

reboot_node() {
  local instance started=$SECONDS before before_id='' info now_id now_epoch
  instance=$(instance_id)

  # An SSM ping or a successful command only proves the agent is reachable,
  # and a pre-reboot ping can look like one after the reboot. The kernel's
  # boot ID changes on every boot, so remember it and wait for a different
  # one. That is the only success. (A boot time compared with this runner's
  # clock is not proof: a skewed node clock can make the pre-reboot boot look
  # new.) If the node can't answer now, still reboot it (that is usually why
  # the button is pressed) but end non-zero, since a restart can't be proven.
  before=$(boot_info 90)
  before_id=${before%% *}
  if [ -n "$before_id" ]; then
    log "boot id before the reboot: $before_id"
  else
    log "could not read the boot id before the reboot (node not answering)"
  fi

  log "rebooting $instance (EC2 RebootInstances: ACPI reboot, forced by AWS after ~4 minutes if the OS ignores it)"
  aws ec2 reboot-instances --region "$REGION" --instance-ids "$instance"

  if [ -z "$before_id" ]; then
    local msg="reboot issued; could not prove the node restarted (no pre-reboot boot ID)"
    echo "::warning::$msg"
    echo "**Warning:** $msg. Check the after-diagnose and the EC2 console." >>"$SUMMARY"
    # Give the node time to come back so the after-diagnose is useful.
    sleep 150
    return 1
  fi

  # Poll for up to 20 minutes. Every probe goes through SSM, so it only
  # succeeds while the agent is up; success needs a new boot.
  local deadline=$((SECONDS + 1200))
  sleep 60
  while [ "$SECONDS" -lt "$deadline" ]; do
    info=$(boot_info 60)
    if [ -n "$info" ]; then
      now_id=${info%% *}
      now_epoch=${info##* }
      if [ "$now_id" != "$before_id" ]; then
        log "node rebooted: boot id $now_id, up since epoch $now_epoch; giving k3s 90s to start before the after-diagnose"
        sleep 90
        echo "Reboot: new boot id seen $((SECONDS - started))s after the request (was ${before_id:-unknown})." >>"$SUMMARY"
        return 0
      fi
      log "node answers but has not rebooted yet (boot id unchanged)"
    else
      log "waiting for the node (no answer from the SSM agent)"
    fi
    sleep 20
  done
  die "no reboot detected within 20 minutes of the request: the boot id never changed (${before_id}) or the node never answered; check the EC2 console (instance status checks)"
}

apply_zram() {
  local script=infra/modules/compute/zram-swap.sh live
  [ -f "$script" ] || die "$script is not in this commit (it arrives with the zram swap change, PR #55)"
  # The node runs the Terraform-applied glassbox-zram-swap document, not a
  # file from this checkout. Make sure the two are the same, so the button
  # runs exactly the script at this workflow's commit.
  live=$(aws ssm get-document --region "$REGION" --name glassbox-zram-swap \
    --query Content --output text | jq -r '.mainSteps[0].inputs.runCommand[1:-1] | join("\n")') ||
    die "could not read the glassbox-zram-swap document (not applied yet?)"
  if [ "$live" != "$(cat "$script")" ]; then
    die "the applied glassbox-zram-swap document differs from $script at this commit; let the Terraform workflow apply main first"
  fi
  log "glassbox-zram-swap matches $script at $(git rev-parse --short HEAD)"
  send_and_wait glassbox-zram-swap '{"mode":["apply"]}' 600 "apply-zram"
}

# restore-snapshot: EC2 "replace root volume" from one of the node's daily
# snapshots. The instance keeps its ID, Elastic IP, private IP and network
# interface; EC2 reboots it onto a new root volume made from the snapshot.
# Everything written after the snapshot (questions asked, cache, Flux's
# progress) is lost; Flux re-applies the deploy branch after the boot.
#
# Fails closed: the inputs are checked before any AWS call, the instance must
# be running, no other replacement may be in flight, and the snapshot must be
# a completed daily snapshot of this instance. Only then is the task created.
# The reboot alarm's actions are paused while a restore reboots the node (a
# slow boot must not trigger a second reboot mid-restore) and resumed on every
# exit path. An alarm left paused shows "False" in the listing above.
REBOOT_ALARM=glassbox-node-reboot
REBOOT_ALARM_PAUSED=0
resume_reboot_alarm() {
  [ "$REBOOT_ALARM_PAUSED" = 1 ] || return 0
  if aws cloudwatch enable-alarm-actions --region "$REGION" --alarm-names "$REBOOT_ALARM"; then
    REBOOT_ALARM_PAUSED=0
    log "re-enabled the actions of $REBOOT_ALARM"
  else
    echo "::error::could not re-enable the actions of $REBOOT_ALARM; it stays paused until re-enabled (run Ops · Restore from snapshot again or fix it in the console)"
    echo "**Error:** the reboot alarm $REBOOT_ALARM is still paused." >>"$SUMMARY"
    return 1
  fi
}

restore_snapshot() {
  local snapshot=${SNAPSHOT:-} old_volume=${OLD_VOLUME:-} instance state snaps chosen
  local delete_args=() task_id deadline task_state last_state='' root_before tasks in_flight fate

  if [ "$snapshot" != latest ] && ! [[ "$snapshot" =~ ^snap-[0-9a-f]{8}([0-9a-f]{9})?$ ]]; then
    die "input snapshot='$(printf '%s' "$snapshot" | redact 80)' must be 'latest' or a snapshot ID (snap-0123456789abcdef0)"
  fi
  require_one_of old_volume "$old_volume" keep delete
  [ "$old_volume" = delete ] && delete_args=(--delete-replaced-root-volume)

  instance=$(instance_id)
  state=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$instance" \
    --query 'Reservations[0].Instances[0].State.Name' --output text)
  [ "$state" = running ] ||
    die "the instance is '$state'; EC2 can replace the root volume of a running instance only"

  tasks=$(aws ec2 describe-replace-root-volume-tasks --region "$REGION" \
    --filters "Name=instance-id,Values=$instance" --output json) ||
    die "could not check for restores already in progress"
  in_flight=$(jq '[.ReplaceRootVolumeTasks[] | select(.TaskState | test("^(pending|in-progress|failing)"))] | length' <<<"$tasks") ||
    die "could not read the restore task list"
  [ "$in_flight" -eq 0 ] ||
    die "a root volume replacement is already in progress for $instance; wait for it (run Ops · List snapshots)"

  snaps=$(daily_snapshots "$instance") || die "could not list the daily snapshots"
  if [ "$snapshot" = latest ]; then
    chosen=$(jq -c '[.[] | select(.state == "completed")] | last // empty' <<<"$snaps")
    [ -n "$chosen" ] || die "no completed daily snapshot of $instance exists yet"
  else
    chosen=$(jq -c --arg s "$snapshot" '.[] | select(.id == $s)' <<<"$snaps")
    [ -n "$chosen" ] ||
      die "$snapshot is not one of this instance's daily snapshots (list them with Ops · List snapshots)"
    [ "$(jq -r .state <<<"$chosen")" = completed ] ||
      die "$snapshot is still $(jq -r .state <<<"$chosen"); wait until it is completed"
  fi
  snapshot=$(jq -r .id <<<"$chosen")

  root_before=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$instance" \
    --query 'Reservations[0].Instances[0].BlockDeviceMappings[0].Ebs.VolumeId' --output text)
  fate="kept, detached (about \$1.60/month until deleted)"
  [ "$old_volume" = delete ] && fate="deleted by EC2 once the replacement succeeds"
  log "restoring $instance from $snapshot (taken $(jq -r .start <<<"$chosen")); current root volume $root_before will be $fate"
  {
    echo "Restore: \`$snapshot\` taken $(jq -r .start <<<"$chosen") onto \`$instance\`."
    echo "Previous root volume \`$root_before\`: $fate."
    echo
  } >>"$SUMMARY"

  # Last step before the change: pause the reboot alarm. If that fails (not
  # applied yet), go on without it: the alarm needs 3 straight minutes of
  # failed checks, which a normal reboot doesn't reach.
  trap 'exit 143' TERM INT
  trap resume_reboot_alarm EXIT
  if aws cloudwatch disable-alarm-actions --region "$REGION" --alarm-names "$REBOOT_ALARM"; then
    REBOOT_ALARM_PAUSED=1
    log "paused the actions of $REBOOT_ALARM for the restore"
  else
    echo "::warning::could not pause $REBOOT_ALARM; restoring anyway"
  fi

  task_id=$(aws ec2 create-replace-root-volume-task --region "$REGION" \
    --instance-id "$instance" --snapshot-id "$snapshot" ${delete_args[@]+"${delete_args[@]}"} \
    --client-token "gh-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}" \
    --query ReplaceRootVolumeTask.ReplaceRootVolumeTaskId --output text) ||
    die "EC2 refused the root volume replacement (nothing was changed)"
  log "replacement task $task_id created; EC2 now creates the volume and reboots the instance"

  # 20 + 10 minutes of waiting, plus the after-diagnose, fit the act job's
  # 60-minute limit and the one-hour OIDC session.
  deadline=$((SECONDS + 1200))
  while [ "$SECONDS" -lt "$deadline" ]; do
    sleep 15
    task_state=$(aws ec2 describe-replace-root-volume-tasks --region "$REGION" \
      --replace-root-volume-task-ids "$task_id" \
      --query 'ReplaceRootVolumeTasks[0].TaskState' --output text 2>/dev/null) || continue
    [ "$task_state" = "$last_state" ] || log "task $task_id: $task_state"
    last_state=$task_state
    case "$task_state" in
      succeeded) break ;;
      failed) die "the replacement failed; EC2 kept the original root volume attached and rebooted the instance" ;;
      failed-detached) die "the replacement failed and the instance may have NO root volume attached; check the EC2 console now" ;;
    esac
  done
  [ "$last_state" = succeeded ] ||
    die "task $task_id still '${last_state:-unknown}' after 20 minutes; it keeps running in EC2, check it with Ops · List snapshots"
  echo "Replacement task \`$task_id\` succeeded." >>"$SUMMARY"

  # Wait for the node to answer through SSM again, then give k3s time to
  # start MySQL, Redis and the app before the after-diagnose.
  deadline=$((SECONDS + 600))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if [ -n "$(boot_info 60)" ]; then
      log "node answers again; giving k3s 90s before the after-diagnose"
      sleep 90
      resume_reboot_alarm || die "restore done, but the reboot alarm is still paused"
      return 0
    fi
    log "waiting for the node to answer (SSM agent)"
    sleep 20
  done
  die "the root volume was replaced, but the node did not answer through SSM within 10 minutes; see the after-diagnose and the EC2 console"
}

act() {
  local action=$1 parameters
  case "$action" in
    reboot-node)
      reboot_node
      ;;
    restart-deployment)
      require_one_of deployment "${DEPLOYMENT:-}" api retrieval-worker traefik
      parameters=$(jq -cn --arg v "$DEPLOYMENT" '{deployment: [$v]}')
      send_and_wait glassbox-ops-restart-deployment "$parameters" 1200 "restart-deployment $DEPLOYMENT"
      ;;
    flux-suspend | flux-resume)
      require_one_of flux_target "${FLUX_TARGET:-}" keda keda-scaling ingest app-ready flux-system helmrelease-keda
      parameters=$(jq -cn --arg v "$FLUX_TARGET" '{target: [$v]}')
      send_and_wait "glassbox-ops-$action" "$parameters" 900 "$action $FLUX_TARGET"
      ;;
    flux-reconcile)
      send_and_wait glassbox-ops-flux-reconcile '{}' 1200 "flux-reconcile"
      ;;
    scale-keda)
      require_one_of keda_replicas "${KEDA_REPLICAS:-}" 0 1
      parameters=$(jq -cn --arg v "$KEDA_REPLICAS" '{replicas: [$v]}')
      send_and_wait glassbox-ops-scale-keda "$parameters" 900 "scale-keda $KEDA_REPLICAS"
      ;;
    cronjob-suspend | cronjob-resume)
      require_one_of cronjob "${CRONJOB:-}" warm-answers
      parameters=$(jq -cn --arg v "$CRONJOB" '{cronjob: [$v]}')
      send_and_wait "glassbox-ops-$action" "$parameters" 600 "$action $CRONJOB"
      ;;
    reindex)
      # The document refuses mid-release, waits up to 16 minutes for the Job
      # and stops itself at 30 (on-node limit 1800 s); wait a minute longer.
      send_and_wait glassbox-ops-reindex '{}' 1860 "reindex"
      ;;
    apply-zram)
      apply_zram
      ;;
    restore-snapshot)
      restore_snapshot
      ;;
    *)
      die "unknown action '$action'"
      ;;
  esac
}

case "${1:-}" in
  diagnose) diagnose "${2:-diagnose}" ;;
  snapshots) aws_overview ;;
  act) act "${2:-}" ;;
  redact) tr '\n' ' ' | redact 200; echo ;;
  *) die "usage: ops-run.sh diagnose [title] | snapshots | act <action> | redact" ;;
esac
