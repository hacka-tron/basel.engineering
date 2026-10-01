#!/usr/bin/env bash
# Offline test for release-provenance.sh against a local bare repository.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
script="$here/../release-provenance.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fail=0

export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.invalid
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.invalid
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

git init -q --bare "$tmp/remote.git"
git clone -q "$tmp/remote.git" "$tmp/work" 2>/dev/null
cd "$tmp/work"
git checkout -q -b main
echo 1 >f && git add f && git commit -qm old
old=$(git rev-parse HEAD)
echo 2 >f && git commit -qam new
new=$(git rev-parse HEAD)
git push -q origin main

expect() { # expect <0|1> <description> <event> <ref> <sha>
  local want=$1 desc=$2 rc=0
  EVENT_NAME=$3 REF=$4 SHA=$5 bash "$script" >/dev/null 2>&1 || rc=$?
  if { [ "$want" -eq 0 ] && [ "$rc" -eq 0 ]; } || { [ "$want" -ne 0 ] && [ "$rc" -ne 0 ]; }; then
    echo "ok   $desc"
  else
    echo "FAIL $desc (exit $rc)"
    fail=1
  fi
}

expect 0 "dispatch at the head of main is allowed" workflow_dispatch refs/heads/main "$new"
expect 1 "dispatch of an older main commit is refused" workflow_dispatch refs/heads/main "$old"
expect 1 "dispatch from another branch is refused" workflow_dispatch refs/heads/feature "$new"
expect 1 "dispatch from a tag is refused" workflow_dispatch refs/tags/v1 "$new"
expect 0 "push runs are not checked" push refs/heads/main "$old"

# main moves on after the run started (run queued behind another release)
echo 3 >f && git commit -qam newer && git push -q origin main
expect 1 "dispatch overtaken by a newer main is refused" workflow_dispatch refs/heads/main "$new"

# remote unreachable: fail closed
rc=0
EVENT_NAME=workflow_dispatch REF=refs/heads/main SHA=$new bash "$script" "$tmp/missing.git" >/dev/null 2>&1 || rc=$?
if [ "$rc" -ne 0 ]; then echo "ok   unreadable remote fails closed"; else echo "FAIL unreadable remote fails closed"; fail=1; fi

exit "$fail"
