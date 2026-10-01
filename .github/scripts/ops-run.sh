#!/usr/bin/env bash
# Runs one runbook action for .github/workflows/ops.yml. Not for local use.
#
#   ops-run.sh diagnose
#   ops-run.sh act <action>
#
# Inputs come from environment variables set by the workflow (never
# interpolated into this script): DEPLOYMENT, FLUX_TARGET, KEDA_REPLICAS,
# CRONJOB. Each is checked against a fixed list here, and SSM checks it
# again against the document's allowedValues.
#
# Everything on the node runs through Terraform-managed SSM documents
# (infra/modules/ops, and glassbox-zram-swap in infra/modules/compute); the
# assumed role can't send any other document.
set -euo pipefail

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
  local instance command_id deadline status invocation result stdout stderr

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
  LAST_STDOUT=$stdout
  if [ "${QUIET:-0}" = 1 ]; then
    [ "$status" = Success ]
    return
  fi
  stderr=$(jq -r '.StandardErrorContent // ""' <<<"$invocation")
  echo "::group::$title output ($status)"
  printf '%s\n' "$stdout"
  [ -z "$stderr" ] || printf -- '--- stderr ---\n%s\n' "$stderr"
  echo "::endgroup::"
  if [ "${#stdout}" -ge 23900 ]; then
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

diagnose() {
  local title=${1:-diagnose}
  send_and_wait glassbox-ops-diagnose '{}' 600 "$title"
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
  local instance rebooted_at started=$SECONDS before before_id='' info now_id now_epoch
  instance=$(instance_id)

  # An SSM ping or a successful command only proves the agent is reachable,
  # and a pre-reboot ping can look like one after the reboot. The kernel's
  # boot ID changes on every boot, so remember it and wait for a different
  # one. If the node can't answer now (the usual reason to reboot it), fall
  # back to its boot time: it must be later than the reboot request.
  before=$(boot_info 90)
  before_id=${before%% *}
  if [ -n "$before_id" ]; then
    log "boot id before the reboot: $before_id"
  else
    log "could not read the boot id before the reboot (node not answering); will require a boot time after the request instead"
  fi

  rebooted_at=$(date -u +%s)
  log "rebooting $instance (EC2 RebootInstances: ACPI reboot, forced by AWS after ~4 minutes if the OS ignores it)"
  aws ec2 reboot-instances --region "$REGION" --instance-ids "$instance"

  # Poll for up to 20 minutes. Every probe goes through SSM, so it only
  # succeeds while the agent is up; success needs a new boot.
  local deadline=$((SECONDS + 1200))
  sleep 60
  while [ "$SECONDS" -lt "$deadline" ]; do
    info=$(boot_info 60)
    if [ -n "$info" ]; then
      now_id=${info%% *}
      now_epoch=${info##* }
      if { [ -n "$before_id" ] && [ "$now_id" != "$before_id" ]; } ||
        { [ -z "$before_id" ] && [ "$now_epoch" -ge "$rebooted_at" ]; }; then
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
  die "no reboot detected within 20 minutes of the request: the boot id never changed (${before_id:-unknown before}) or the node never answered; check the EC2 console (instance status checks)"
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
    apply-zram)
      apply_zram
      ;;
    *)
      die "unknown action '$action'"
      ;;
  esac
}

case "${1:-}" in
  diagnose) diagnose "${2:-diagnose}" ;;
  act) act "${2:-}" ;;
  *) die "usage: ops-run.sh diagnose [title] | act <action>" ;;
esac
