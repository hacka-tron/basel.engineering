#!/usr/bin/env bash
# Offline test for redact() in ../scripts/lib.sh. Needs no cluster or AWS.
#   bash infra/modules/ops/tests/redact-test.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=../scripts/lib.sh
source "$here/../scripts/lib.sh"

fail=0
# expect_absent <label> <secret> <input>: the secret must not survive.
expect_absent() {
  local out
  out=$(printf '%s\n' "$3" | redact)
  if grep -qF -- "$2" <<<"$out"; then
    echo "FAIL $1: '$2' survived: $out"
    fail=1
  else
    echo "ok   $1 -> $out"
  fi
}
# expect_present <label> <text> <input>: harmless text must survive.
expect_present() {
  local out
  out=$(printf '%s\n' "$3" | redact)
  if grep -qF -- "$2" <<<"$out"; then
    echo "ok   $1 -> $out"
  else
    echo "FAIL $1: '$2' was lost: $out"
    fail=1
  fi
}

expect_absent userinfo hunter2 'failed to clone https://deploy:hunter2@github.com/o/r.git'  # pragma: allowlist secret
expect_absent url-token-user ghp_abc 'pull https://ghp_abcd@github.com/o/r'
expect_absent query-string SIGSECRET 'GET https://bucket.s3.amazonaws.com/o?X-Amz-Signature=SIGSECRET&X-Amz-Date=1'
expect_absent password-eq s3cr3tpw 'login failed password=s3cr3tpw for user'
expect_absent password-colon s3cr3tpw 'Password: s3cr3tpw'
expect_absent json-token tokvalue '{"token":"tokvalue","a":1}'
expect_absent api-key abc123 'apiKey=abc123 rejected'
expect_absent secret-name sv1 'client_secret: sv1'
expect_absent authorization-bearer BEARERVAL 'Authorization: Bearer BEARERVAL'
expect_absent jwt-header eyJhbGciOiJIUzI1NiJ9 'bad token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig-nature_x'  # pragma: allowlist secret
expect_absent base64 QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo= 'blob QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo= end'
expect_absent hex 0123456789abcdef0123456789abcdef 'sha 0123456789abcdef0123456789abcdef'
expect_absent aws-key AKIAIOSFODNN7EXAMPLE 'key id AKIAIOSFODNN7EXAMPLE seen'  # pragma: allowlist secret
expect_present pod-name retrieval-worker-5d9c8b7f6-abcde 'Back-off restarting retrieval-worker-5d9c8b7f6-abcde'
expect_present plain-text 'node memory pressure' 'node memory pressure at 12:00'

# Truncation: default 160, explicit width honoured, applied after masking.
long=$(printf 'x%.0s ' $(seq 1 200))
[ "$(printf '%s\n' "$long" | redact | wc -c | tr -d ' ')" -le 161 ] || {
  echo "FAIL default truncation"
  fail=1
}
[ "$(printf '%s\n' "$long" | redact 50 | wc -c | tr -d ' ')" -le 51 ] || {
  echo "FAIL width argument"
  fail=1
}

exit "$fail"
