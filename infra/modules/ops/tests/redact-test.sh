#!/usr/bin/env bash
# Offline test for redact() in ../scripts/lib.sh. Needs no cluster or AWS.
#   bash infra/modules/ops/tests/redact-test.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=../scripts/lib.sh
source "$here/../scripts/lib.sh"
# lib.sh pins PATH. REDACT_TEST_PATH (a directory with awk/sed shims) lets
# the suite run under another awk or sed, e.g. mawk or GNU sed on macOS.
[ -z "${REDACT_TEST_PATH:-}" ] || PATH=$REDACT_TEST_PATH:$PATH

fail=0
# A failing helper call (e.g. a typo'd expect_* name) must fail the suite,
# not just print "command not found" and drop that check.
trap 'echo "FAIL line $LINENO: command failed"; fail=1' ERR
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

# expect_same <label> <input>: output must equal input.
expect_same() {
  local out
  out=$(printf '%s\n' "$2" | redact 220)
  if [ "$out" = "$2" ]; then
    echo "ok   $1 (unchanged)"
  else
    echo "FAIL $1: changed:"
    diff <(printf '%s\n' "$2") <(printf '%s\n' "$out")
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

# Multiline: the key is on one line and its value on the next.
expect_absent yaml-next-line-indented hunter2 $'password:\n  hunter2'  # pragma: allowlist secret
expect_absent yaml-next-line-flush hunter2 $'Password:\nhunter2'  # pragma: allowlist secret
expect_absent yaml-block-scalar line2secret $'db_password: |\n  line1secret\n  line2secret\nport: 3306'  # pragma: allowlist secret
expect_present yaml-block-scalar-end 'port: 3306' $'db_password: |\n  line1secret\nport: 3306'  # pragma: allowlist secret
expect_absent yaml-folded tokpart $'api_token: >-\n    tokpart1\n    tokpart2'  # pragma: allowlist secret
expect_absent yaml-sequence seqsecret $'credentials:\n- seqsecret'  # pragma: allowlist secret
expect_absent env-style envsecret $'TOKEN=\nenvsecret'  # pragma: allowlist secret
expect_absent json-pretty-value jsonsecret $'{\n  "password":\n    "jsonsecret",\n  "user": "bob"\n}'  # pragma: allowlist secret
expect_absent json-pretty-object nestedsecret $'{\n  "credentials": {\n    "user": "bob",\n    "pass": "nestedsecret"\n  },\n  "ok": true\n}'  # pragma: allowlist secret
expect_present json-pretty-object-after '"ok": true' $'{\n  "credentials": {\n    "pass": "nestedsecret"\n  },\n  "ok": true\n}'  # pragma: allowlist secret
expect_present crlf-input 'user: bob' $'password:\r\n  crlfsecret\r\nuser: bob'  # pragma: allowlist secret
expect_absent crlf-secret crlfsecret $'password:\r\n  crlfsecret\r\nuser: bob'  # pragma: allowlist secret

# Review round 1: a value that starts on the key's line and continues on
# the next (open quotes, folded scalars, Bearer then the token).
expect_absent cont-double-quote LEAKdq1 $'password: "abc\n  LEAKdq1 more"\nuser: bob'  # pragma: allowlist secret
expect_present cont-double-quote-after 'user: bob' $'password: "abc\n  LEAKdq1 more"\nuser: bob'  # pragma: allowlist secret
expect_absent cont-single-quote LEAKsq1 $'token: \'abc\n  LEAKsq1\''  # pragma: allowlist secret
expect_absent cont-folded-scalar LEAKfold $'client_secret: first part\n  LEAKfold second part'  # pragma: allowlist secret
expect_absent bearer-next-line LEAKbearer $'Authorization: Bearer\n  LEAKbearer'  # pragma: allowlist secret
# A flush-left value shaped like "word=" or "word:" is still a value.
expect_absent flush-padded-base64 aHVudGVyMg== $'Password:\naHVudGVyMg=='  # pragma: allowlist secret
expect_absent flush-single-pad YWRtaW4= $'Password:\nYWRtaW4='  # pragma: allowlist secret
expect_absent flush-colon-value ab:cd $'token:\nab:cd'  # pragma: allowlist secret
expect_same flush-env-sibling $'TOKEN=\nNEXT=value'
# Tabs count as 8 columns, so tab-indented lines under a space-indented key
# are still its block.
expect_absent tab-indent LEAKtab $'    password:\n\tfirst\n\tLEAKtab'  # pragma: allowlist secret
# A PEM BEGIN on the same line as the previous END.
expect_absent pem-back-to-back LEAKpem2 $'-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE----------BEGIN PRIVATE KEY-----\nLEAKpem2\n-----END PRIVATE KEY-----\nafter'  # pragma: allowlist secret
expect_present pem-back-to-back-after 'after' $'-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE----------BEGIN PRIVATE KEY-----\nLEAKpem2\n-----END PRIVATE KEY-----\nafter'  # pragma: allowlist secret

# kubectl get secret -o yaml / -o json: short base64 values (under the
# 20-character rule) must not survive, but the key names and the rest do.
secret_yaml=$'apiVersion: v1\ndata:\n  username: YWRtaW4=\n  ca.crt: Y2E=\n  config: |\n    c2hvcnQ=\nkind: Secret\nmetadata:\n  name: app-db\ntype: Opaque'  # pragma: allowlist secret
expect_absent secret-yaml-username YWRtaW4= "$secret_yaml"
expect_absent secret-yaml-dotted Y2E= "$secret_yaml"
expect_absent secret-yaml-nested c2hvcnQ= "$secret_yaml"
expect_present secret-yaml-key-name 'username' "$secret_yaml"
expect_present secret-yaml-after 'name: app-db' "$secret_yaml"
secret_list=$'items:\n- apiVersion: v1\n  data:\n    user: Ym9i\n  kind: Secret\n- apiVersion: v1\n  stringData:\n    pw: plainpw\n  kind: Secret'  # pragma: allowlist secret
expect_absent secret-list-data Ym9i "$secret_list"
expect_absent secret-list-stringdata plainpw "$secret_list"
expect_present secret-list-kind 'kind: Secret' "$secret_list"
secret_json=$'{\n    "apiVersion": "v1",\n    "data": {\n        "user": "Ym9i"\n    },\n    "kind": "Secret"\n}'  # pragma: allowlist secret
expect_absent secret-json Ym9i "$secret_json"
expect_present secret-json-kind '"kind": "Secret"' "$secret_json"
expect_absent secret-json-oneline Ym9i '{"kind":"Secret","data":{"user":"Ym9i","x":"eA=="}}'  # pragma: allowlist secret
expect_absent secret-annotation Ym9i $'  annotations:\n    kubectl.kubernetes.io/last-applied-configuration: |\n      {"apiVersion":"v1","data":{"user":"Ym9i"},"kind":"Secret"}'  # pragma: allowlist secret

# PEM blocks, on their own, inside YAML, on one line, and unterminated.
pem=$'-----BEGIN PRIVATE KEY-----\nMIIBVgIBADANBg\nshortpemtail\n-----END PRIVATE KEY-----\nafter pem'  # pragma: allowlist secret
expect_absent pem-body MIIBVgIBADANBg "$pem"
expect_absent pem-tail shortpemtail "$pem"
expect_present pem-after 'after pem' "$pem"
expect_absent pem-in-yaml pemyamltail $'tls.key: |\n  -----BEGIN RSA PRIVATE KEY-----\n  pemyamltail\n  -----END RSA PRIVATE KEY-----\nkind: Secret'  # pragma: allowlist secret
expect_absent pem-one-line onelinepem 'cert -----BEGIN CERTIFICATE----- onelinepem -----END CERTIFICATE----- ok'  # pragma: allowlist secret
expect_present pem-one-line-rest 'ok' 'cert -----BEGIN CERTIFICATE----- onelinepem -----END CERTIFICATE----- ok'  # pragma: allowlist secret
expect_absent pem-unterminated unterminated $'-----BEGIN PRIVATE KEY-----\nunterminated\nmore'  # pragma: allowlist secret

# Negative cases: ordinary diagnose output must come through readable.
expect_same flux-table $'NAMESPACE     NAME          AGE   READY   STATUS\nflux-system   flux-system   3d    True    Applied revision: deploy@sha1:abc1234\napp           app-ready     3d    False   health check failed after 2m: timeout waiting for: [Deployment/app/api status: \'InProgress\']'
expect_same section-and-free $'\n===== memory (MiB) =====\n               total        used        free      shared  buff/cache   available\nMem:            1843        1502          88           2         252         211\nSwap:           2047         812        1235'
expect_same events-table $'NS    LAST                   COUNT   REASON      KIND   NAME\napp   2026-10-01T10:00:00Z   3       BackOff     Pod    retrieval-worker-5d9c8b7f6-abcde\napp   2026-10-01T10:01:00Z   1       Unhealthy   Pod    api-7c9d5f-xyz12'
expect_same yaml-null-key $'password:\nusername: bob\nport: 3306'
expect_same oom-line '2026-10-01T09:58:12+0000 ip-10-0-1-5 kernel: Out of memory: Killed process 4242 (mysqld) total-vm:1234kB, anon-rss:567kB'
expect_same reconcile-log 'True Applied revision: deploy@sha1:abc1234'
expect_same list-items $'- name: api\n  ready: true\n- name: worker\n  ready: false'

# Fail closed: if a pass fails, the rest is withheld, not printed raw.
out=$(awk() { return 2; }; printf 'password:\n  failsecret\n' | redact)  # pragma: allowlist secret
if grep -qF failsecret <<<"$out" || ! grep -qF withheld <<<"$out"; then
  echo "FAIL awk failure must withhold output: $out"
  fail=1
else
  echo "ok   awk-failure -> $out"
fi

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
