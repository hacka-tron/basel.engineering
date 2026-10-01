#!/usr/bin/env bash
# Merges main into the deploy branch and pushes it, retrying when someone
# else (Flux's ImageUpdateAutomation) pushed deploy in the meantime. Used by
# sync-deploy-branch.yml.
#
# Each attempt starts from a fresh fetch of both branches, rebuilds the merge
# on top of whatever deploy is now, and pushes without force. A rejected
# (non-fast-forward) push is retried up to SYNC_MAX_ATTEMPTS times; it is
# never force-pushed, because that would drop Flux's image-tag commit. A
# merge conflict is not retried: it needs a human.
#
# Exit codes: 0 pushed or already up to date, 1 gave up after retries,
# 2 merge conflict.
#
#   [SYNC_REMOTE=origin] [SYNC_SOURCE=main] [SYNC_TARGET=deploy] \
#   [SYNC_MAX_ATTEMPTS=5] [SYNC_RETRY_DELAY=5] sync-deploy-branch.sh
set -euo pipefail

remote=${SYNC_REMOTE:-origin}
source_branch=${SYNC_SOURCE:-main}
target=${SYNC_TARGET:-deploy}
max_attempts=${SYNC_MAX_ATTEMPTS:-5}
delay=${SYNC_RETRY_DELAY:-5}

for ((attempt = 1; attempt <= max_attempts; attempt++)); do
  echo "Attempt $attempt of $max_attempts"
  git fetch --no-tags "$remote" \
    "+refs/heads/$source_branch:refs/remotes/$remote/$source_branch" \
    "+refs/heads/$target:refs/remotes/$remote/$target"
  # Start over from the remote's deploy, discarding a previous attempt's merge.
  git checkout -q -B "$target" "refs/remotes/$remote/$target"

  if git merge-base --is-ancestor "refs/remotes/$remote/$source_branch" HEAD; then
    echo "$target already contains $source_branch; nothing to push."
    exit 0
  fi

  if ! git merge --no-edit -m "sync: merge $source_branch into $target" \
    "refs/remotes/$remote/$source_branch"; then
    git merge --abort || true
    echo "::error::Merging $source_branch into $target conflicts. Resolve it by hand; not retrying."
    exit 2
  fi

  # Plain push, no --force and no '+' refspec: if deploy moved since the
  # fetch, the remote rejects it and the next attempt merges onto the new tip.
  if git push "$remote" "HEAD:refs/heads/$target"; then
    echo "Pushed $target on attempt $attempt."
    exit 0
  fi

  if ((attempt < max_attempts)); then
    wait_s=$((delay * attempt))
    echo "Push rejected (probably a concurrent Flux commit); retrying in ${wait_s}s."
    sleep "$wait_s"
  fi
done

echo "::error::Could not push $target after $max_attempts attempts; it does not contain the latest $source_branch yet. Re-run this workflow."
exit 1
