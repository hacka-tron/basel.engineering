#!/usr/bin/env bash
# Prints code=true unless every file changed between $1 and $2 is a process
# doc (project/** except project/SNAPSHOT.md, or a repo-root *.md). Fails
# open: an unknown base (new branch, force push, shallow history) means code=true.
set -euo pipefail
base="${1:-}" head="${2:-HEAD}"
if [[ -z "$base" || "$base" =~ ^0+$ ]] || ! files=$(git diff --name-only "$base" "$head" 2>/dev/null); then
  echo "code=true"; exit 0
fi
[[ -z "$files" ]] && { echo "code=true"; exit 0; }
while IFS= read -r f; do
  case "$f" in
    project/SNAPSHOT.md) echo "code=true"; exit 0 ;;
    project/*) ;;
    */*) echo "code=true"; exit 0 ;;
    *.md) ;;
    *) echo "code=true"; exit 0 ;;
  esac
done <<< "$files"
echo "code=false"
