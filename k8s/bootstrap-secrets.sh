#!/usr/bin/env bash
set -euo pipefail
umask 077

# Run on the k3s node with its instance role and k3s kubectl configured.
for namespace in app data; do
  kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f -
done

mysql_password=$(aws ssm get-parameter \
  --name /glassbox/mysql/password \
  --with-decryption \
  --query Parameter.Value \
  --output text)
if [[ -z "$mysql_password" || "$mysql_password" == "None" ]]; then
  echo "SSM returned an empty MySQL password" >&2
  exit 1
fi
for namespace in app data; do
  kubectl -n "$namespace" create secret generic glassbox-mysql \
    --from-literal=mysql-password="$mysql_password" \
    --dry-run=client -o yaml | kubectl apply -f -
done
unset mysql_password

ip_hash_salt=$(aws ssm get-parameter \
  --name /glassbox/ip_hash_salt \
  --with-decryption \
  --query Parameter.Value \
  --output text)
if [[ -z "$ip_hash_salt" || "$ip_hash_salt" == "None" ]]; then
  echo "SSM returned an empty IP hash salt" >&2
  exit 1
fi
kubectl -n app create secret generic glassbox-app \
  --from-literal=ip-hash-salt="$ip_hash_salt" \
  --dry-run=client -o yaml | kubectl apply -f -
unset ip_hash_salt
