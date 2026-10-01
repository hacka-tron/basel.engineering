#!/usr/bin/env bash
# Refuses a manual (workflow_dispatch) release whose commit is not the
# current head of main. Used by release.yml before anything is built.
#
# Why: Flux's ImagePolicy deploys the highest build-N tag, and N is
# github.run_number, which grows with every run whatever commit it builds.
# A manual run against an old ref or a feature branch would therefore mint
# the highest number and Flux would roll production back (or sideways) to
# that commit. Push runs are not checked: they only fire on main, and the
# release-main concurrency group runs them in order, so a newer commit
# always gets a higher number.
#
# The check is exact equality, not "is an ancestor of main": every old
# release is an ancestor of main, so an ancestor check would let exactly
# the stale build this exists to stop through.
#
#   EVENT_NAME=workflow_dispatch REF=refs/heads/main SHA=<sha> \
#     release-provenance.sh [remote]
set -euo pipefail

remote=${1:-origin}
: "${EVENT_NAME:?EVENT_NAME is required}"
: "${REF:?REF is required}"
: "${SHA:?SHA is required}"

if [ "$EVENT_NAME" != "workflow_dispatch" ]; then
  echo "Release provenance: $EVENT_NAME run, no check needed."
  exit 0
fi

if [ "$REF" != "refs/heads/main" ]; then
  echo "::error::Manual releases must run on main, not $REF. A build from another ref would get the highest build number and Flux would deploy it."
  exit 1
fi

if ! head=$(git ls-remote --exit-code "$remote" refs/heads/main | cut -f1) || [ -z "$head" ]; then
  echo "::error::Could not read the current head of main from $remote; refusing to release."
  exit 1
fi

if [ "$head" != "$SHA" ]; then
  echo "::error::This run builds $SHA but main is now at $head. Refusing: a stale build would get the highest build number and Flux would deploy it. Start a new run on main."
  exit 1
fi

echo "Release provenance: $SHA is the current head of main."
