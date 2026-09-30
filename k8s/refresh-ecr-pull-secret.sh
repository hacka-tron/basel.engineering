#!/usr/bin/env bash
set -euo pipefail
umask 077

# Run on the k3s node with its instance role and k3s kubectl configured.
# ECR authorization tokens expire after 12 hours; this must run on a
# recurring schedule (a systemd timer on the node - see infra/CI.md), not
# just once at bring-up.
region="us-east-1"
account_id="404379474987"

password=$(aws ecr get-login-password --region "$region")
if [[ -z "$password" ]]; then
  echo "aws ecr get-login-password returned empty" >&2
  exit 1
fi

kubectl -n app create secret docker-registry regcred \
  --docker-server="${account_id}.dkr.ecr.${region}.amazonaws.com" \
  --docker-username=AWS \
  --docker-password="$password" \
  --dry-run=client -o yaml | kubectl apply -f -
unset password
