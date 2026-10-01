#!/usr/bin/env bash
# Offline test for sync-deploy-branch.sh against a local bare repository.
# A `git` shim on PATH makes a competing "Flux" push to deploy just before
# the script's own push, for the first $RACES pushes. That produces a real
# non-fast-forward rejection, so the test checks the retry path, that
# nothing is force-pushed over Flux's commits, the attempt bound, and that
# conflicts are not retried.
# Helpers below are called through check "$@", which shellcheck can't see.
# shellcheck disable=SC2329
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
sync="$here/../sync-deploy-branch.sh"
real_git=$(command -v git)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fail=0

export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.invalid
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.invalid
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

check() { # check <description> <command...>
  local desc=$1
  shift
  if "$@"; then echo "ok   $desc"; else echo "FAIL $desc"; fail=1; fi
}

g() { "$real_git" "$@"; }

# Shim: before each `git push`, if fewer than RACES competing pushes have
# happened, commit a new image tag on deploy from another clone and push it.
mkdir "$tmp/shim"
cat >"$tmp/shim/git" <<EOF
#!/usr/bin/env bash
if [ "\$1" = push ]; then
  n=\$(cat "$tmp/races_done")
  echo \$((\$(cat "$tmp/pushes") + 1)) >"$tmp/pushes"
  if [ "\$n" -lt "\${RACES:-0}" ]; then
    (
      cd "$tmp/flux"
      "$real_git" fetch -q origin deploy
      "$real_git" checkout -q -B deploy origin/deploy
      echo "tag: build-\$((100 + n))" >tag.txt
      "$real_git" commit -qam "deploy: automated image tag update \$n"
      "$real_git" push -q origin deploy
    ) >/dev/null 2>&1
    echo \$((n + 1)) >"$tmp/races_done"
  fi
fi
exec "$real_git" "\$@"
EOF
chmod +x "$tmp/shim/git"

# setup <name>: fresh remote with main (app.txt, tag.txt) and deploy
# (main + one Flux tag commit), then one new main commit to sync.
setup() {
  local d="$tmp/$1"
  mkdir -p "$d"
  g init -q --bare "$d/remote.git"
  g clone -q "$d/remote.git" "$d/seed" 2>/dev/null
  (
    cd "$d/seed"
    g checkout -q -b main
    echo "app v1" >app.txt
    echo "tag: build-1" >tag.txt
    g add . && g commit -qm "initial"
    g push -q origin main
    g checkout -q -b deploy
    echo "tag: build-2" >tag.txt
    g commit -qam "deploy: automated image tag update"
    g push -q origin deploy
    g checkout -q main
    echo "app v2" >app.txt
    g commit -qam "feature on main"
    g push -q origin main
  )
  g clone -q "$d/remote.git" "$d/work" 2>/dev/null
  g clone -q "$d/remote.git" "$d/flux" 2>/dev/null
  echo 0 >"$tmp/races_done"
  echo 0 >"$tmp/pushes"
  rm -f "$tmp/flux"
  ln -s "$d/flux" "$tmp/flux"
  WORK="$d/work"
  REMOTE="$d/remote.git"
}

run_sync() { # run_sync <races> <max_attempts>; sets RC
  RC=0
  (cd "$WORK" && RACES=$1 SYNC_MAX_ATTEMPTS=$2 SYNC_RETRY_DELAY=0 \
    PATH="$tmp/shim:$PATH" bash "$sync") >"$tmp/out" 2>&1 || RC=$?
}

rev() { g --git-dir="$REMOTE" rev-parse "$1"; }
is_ancestor() { g --git-dir="$REMOTE" merge-base --is-ancestor "$1" "$2"; }
flux_commits() { g --git-dir="$REMOTE" log --format=%H --grep='automated image tag update' "$1"; }
all_kept() { # every Flux commit ever pushed is still in deploy
  local c
  for c in $(g -C "$tmp/flux" log --format=%H --grep='automated image tag update' origin/deploy); do
    is_ancestor "$c" deploy || return 1
  done
}

echo "# no race"
setup norace
run_sync 0 5
check "exits 0" [ "$RC" -eq 0 ]
check "deploy contains main" is_ancestor main deploy
check "one push" [ "$(cat "$tmp/pushes")" -eq 1 ]
check "Flux tag kept" [ "$(g --git-dir="$REMOTE" show deploy:tag.txt)" = "tag: build-2" ]

echo "# two racing Flux pushes, five attempts"
setup race2
run_sync 2 5
check "exits 0" [ "$RC" -eq 0 ]
check "three pushes (two rejected)" [ "$(cat "$tmp/pushes")" -eq 3 ]
check "rejection was non-fast-forward" grep -qE 'rejected|fetch first|non-fast-forward' "$tmp/out"
check "deploy contains main" is_ancestor main deploy
check "both racing Flux commits kept" all_kept
check "racing commits counted" [ "$(flux_commits deploy | wc -l | tr -d ' ')" -eq 3 ]
check "latest Flux tag kept" [ "$(g --git-dir="$REMOTE" show deploy:tag.txt)" = "tag: build-101" ]

echo "# Flux wins every time, three attempts"
setup always
run_sync 99 3
check "exits 1" [ "$RC" -eq 1 ]
check "stopped after three pushes" [ "$(cat "$tmp/pushes")" -eq 3 ]
check "deploy is Flux's last commit (not overwritten)" \
  [ "$(rev deploy)" = "$(g -C "$tmp/flux" rev-parse HEAD)" ]
check "all Flux commits kept" all_kept
check "main not merged" bash -c "! git --git-dir='$REMOTE' merge-base --is-ancestor main deploy"

echo "# already up to date"
setup uptodate
run_sync 0 5
before=$(rev deploy)
run_sync 0 5
check "exits 0" [ "$RC" -eq 0 ]
check "no new push" [ "$(rev deploy)" = "$before" ]

echo "# merge conflict"
setup conflict
(
  cd "$tmp/conflict/seed"
  g checkout -q main
  echo "tag: build-50" >tag.txt
  g commit -qam "main edits the tag line too"
  g push -q origin main
)
before=$(rev deploy)
run_sync 0 5
check "exits 2" [ "$RC" -eq 2 ]
check "no push attempted" [ "$(cat "$tmp/pushes")" -eq 0 ]
check "deploy unchanged" [ "$(rev deploy)" = "$before" ]

echo "# script never force-pushes"
check "no --force or + refspec in push" \
  bash -c "! grep -vE '^[[:space:]]*#' '$sync' | grep -E 'push.*(--force|-f |\"\\+)'"

exit "$fail"
