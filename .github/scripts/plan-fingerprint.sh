#!/usr/bin/env bash
# Prints a SHA-256 fingerprint of what a saved Terraform plan would change,
# plus (to stderr) a one-line-per-change summary. Used by bootstrap.yml: the
# apply job re-plans after approval and refuses to apply unless its plan's
# fingerprint equals the one the owner reviewed. The plan file itself never
# leaves the runner (this repository's artifacts and logs are public).
#
#   plan-fingerprint.sh <terraform-root> <plan-file>
set -euo pipefail

root=$1
plan=$2
json=$(terraform -chdir="$root" show -json "$plan")

jq -r '.resource_changes[]? | select(.change.actions != ["no-op"])
  | "  \(.change.actions | join("/")) \(.address)"' <<<"$json" >&2

jq -S '{
  resource_changes: [
    .resource_changes[]? | select(.change.actions != ["no-op"])
    | {address, actions: .change.actions, before: .change.before,
       after: .change.after, after_unknown: .change.after_unknown}
  ],
  output_changes: ((.output_changes // {}) | with_entries(select(.value.actions != ["no-op"])))
}' <<<"$json" | sha256sum | cut -d' ' -f1
