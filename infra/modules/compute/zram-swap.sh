#!/usr/bin/env bash
# Compressed-RAM (zram) swap for the single k3s node.
#
# Delivered and run by the Terraform-managed SSM association
# `glassbox-zram-swap` (infra/modules/compute/zram.tf) - on creation, on every
# document change, and weekly to self-heal. Safe to re-run: it only rewrites
# files whose content differs and never turns swap off.
#
#   Usage: zram-swap.sh [apply|dry-run]     (default: apply)
#
# What it does:
#   1. Writes /etc/systemd/zram-generator.conf, overriding AL2023's packaged
#      default (/usr/lib/systemd/zram-generator.conf sets
#      host-memory-limit=800, which disables zram on this 1.84 GiB node).
#      zram-generator (installed on AL2023 by default) then creates
#      /dev/zram0 on every boot: half of RAM, swap priority 100.
#   2. Leaves /swapfile alone (priority -2): the kernel fills zram first and
#      only overflows to the EBS-backed swap file when zram is full.
#   3. Activates zram0 now if it is not already an active swap device.
#   4. Only once /dev/zram0 is confirmed active: persists zram-oriented VM
#      sysctls in /etc/sysctl.d/ and applies them. An EXIT trap covers every
#      failure path: if zram0 is not active swap when the script exits, it
#      fails the run and removes any sysctl file an earlier run left
#      (restoring AL2023 defaults), so the node never favors swapping with
#      disk swap only.
#
# Rollback (manual, as root on the node) - see docs/DESIGN.md section 9.7.
set -euo pipefail

# Everything runs inside main() so bash parses the whole script before
# executing any of it: the SSM document pipes this file into `bash -s`, and
# a command that read stdin would otherwise swallow the rest of the script.
# (Body intentionally not indented: it contains multi-line file contents.)
main() {
MODE="${1:-apply}"
case "$MODE" in
  apply | --apply) DRY_RUN=0 ;;
  dry-run | --dry-run) DRY_RUN=1 ;;
  *)
    echo "usage: $0 [apply|dry-run]" >&2
    exit 2
    ;;
esac

ZRAM_CONF=/etc/systemd/zram-generator.conf
SYSCTL_CONF=/etc/sysctl.d/99-glassbox-zram.conf
ZRAM_DEV=/dev/zram0
SWAP_UNIT=dev-zram0.swap
SETUP_UNIT=systemd-zram-setup@zram0.service

log() { echo "[zram] $*"; }

run() {
  if [ "$DRY_RUN" = 1 ]; then
    log "dry-run: would run: $*"
  else
    "$@"
  fi
}

# write_if_changed PATH CONTENT -> returns 0 if the file was (or would be)
# changed, 1 if it already had exactly this content.
write_if_changed() {
  local path="$1" content="$2"
  if [ -f "$path" ] && [ "$(cat "$path")" = "$content" ]; then
    log "unchanged: $path"
    return 1
  fi
  if [ "$DRY_RUN" = 1 ]; then
    log "dry-run: would write $path:"
    printf '%s\n' "$content" | sed 's/^/    /'
    return 0
  fi
  local tmp
  tmp="$(mktemp "${path}.XXXXXX")"
  printf '%s\n' "$content" >"$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$path"
  log "wrote: $path"
  return 0
}

zram_swap_active() {
  swapon --show=NAME --noheadings 2>/dev/null | grep -qx "$ZRAM_DEV"
}

# Pick the best compressor this kernel's zram offers: zstd > lz4 > lzo-rle >
# lzo. Since Linux 6.12 zram has its own backends (CONFIG_ZRAM_BACKEND_*),
# and the AL2023 6.18 kernel builds only LZO, so this resolves to lzo-rle
# today; it picks up zstd automatically if a later kernel enables it.
pick_algorithm() {
  local avail=""
  if [ -r /sys/block/zram0/comp_algorithm ]; then
    avail="$(tr -d '[]' </sys/block/zram0/comp_algorithm)"
  else
    # Module not loaded (dry-run doesn't load it): read the kernel config.
    local cfg
    cfg="/boot/config-$(uname -r)"
    if [ -r "$cfg" ]; then
      grep -q '^CONFIG_ZRAM_BACKEND_ZSTD=y' "$cfg" && avail="$avail zstd"
      grep -q '^CONFIG_ZRAM_BACKEND_LZ4=y' "$cfg" && avail="$avail lz4"
      grep -q '^CONFIG_ZRAM_BACKEND_LZO=y' "$cfg" && avail="$avail lzo-rle lzo"
    fi
  fi
  local a
  for a in zstd lz4 lzo-rle lzo; do
    case " $avail " in
      *" $a "*)
        echo "$a"
        return 0
        ;;
    esac
  done
  echo "" # unknown: leave the kernel default
}

if [ "$(id -u)" != 0 ]; then
  echo "[zram] must run as root" >&2
  exit 1
fi

# Remove the zram sysctl file, restore AL2023 defaults, then VERIFY the result
# (file gone, live values equal the defaults). Prints ROLLBACK FAILED naming
# each problem and returns 1 if anything is still wrong.
rollback_sysctls() {
  local bad="" kv k v cur
  if [ -e "$SYSCTL_CONF" ]; then
    rm -f "$SYSCTL_CONF" 2>/dev/null
    if [ -e "$SYSCTL_CONF" ]; then
      bad="$bad $SYSCTL_CONF still exists;"
    else
      echo "[zram] removed $SYSCTL_CONF from an earlier run" >&2
    fi
  fi
  for kv in vm.swappiness=60 vm.page-cluster=3 vm.watermark_boost_factor=15000 vm.watermark_scale_factor=10; do
    k="${kv%%=*}" v="${kv#*=}"
    sysctl -w "$kv" >/dev/null 2>&1 || true
    cur="$(sysctl -n "$k" 2>/dev/null || echo unreadable)"
    [ "$cur" = "$v" ] || bad="$bad $k=$cur (want $v);"
  done
  if [ -n "$bad" ]; then
    echo "[zram] ROLLBACK FAILED:$bad zram-tuned VM settings may still be in effect" >&2
    return 1
  fi
  echo "[zram] AL2023 default VM sysctls verified in effect" >&2
}

# Safety net for EVERY exit path in apply mode, including `set -e` aborts
# anywhere below: if /dev/zram0 is not an active swap device when the script
# exits, the swap-happy sysctls must not stay in effect - with only the EBS
# /swapfile they would push more pages to disk. So remove the sysctl file
# (if an earlier successful run left it, e.g. zram broke after a reboot),
# restore AL2023's defaults, and make sure the run reports failure.
on_exit() {
  local rc=$?
  trap - EXIT
  if [ "$DRY_RUN" = 0 ] && ! zram_swap_active; then
    echo "[zram] ERROR: $ZRAM_DEV is not an active swap device; zram sysctls NOT in effect" >&2
    systemctl --no-pager status "$SETUP_UNIT" "$SWAP_UNIT" 2>&1 | tail -20 >&2 || true
    rollback_sysctls || rc=1
    # Pre-existing gap: the script never disables /swapfile, but if it was
    # already off (and zram failed too) the node has no swap at all. Try to
    # turn it back on and say so.
    if [ -z "$(swapon --show=NAME --noheadings 2>/dev/null)" ] && [ -f /swapfile ]; then
      if swapon /swapfile >&2 2>&1; then
        echo "[zram] no swap was active; re-enabled /swapfile" >&2
      else
        echo "[zram] WARNING: no swap is active and /swapfile could not be re-enabled" >&2
      fi
    fi
    swapon --show >&2 || true
    if [ "$rc" = 0 ]; then rc=1; fi
  fi
  exit "$rc"
}
trap on_exit EXIT

log "mode=$MODE kernel=$(uname -r)"

# 1. zram-generator ships with AL2023; install it if an image ever lacks it.
if ! rpm -q zram-generator >/dev/null 2>&1; then
  run dnf -y install zram-generator
fi

# Load the module so its sysfs reports the available compressors. Loading
# creates an unused /dev/zram0, which the generator's setup unit then
# configures. Harmless if already loaded.
if [ ! -e /sys/block/zram0 ]; then
  run modprobe zram
fi

ALG="$(pick_algorithm)"
log "compression algorithm: ${ALG:-kernel default}"

ZRAM_CONTENT="# Managed by Terraform: infra/modules/compute/zram-swap.sh, delivered by the
# SSM association glassbox-zram-swap. Hand edits are overwritten.
# Overrides AL2023's /usr/lib/systemd/zram-generator.conf, whose
# host-memory-limit=800 disables zram on this 1.84 GiB node.
[zram0]
host-memory-limit = none
zram-size = min(ram / 2, 1024)
swap-priority = 100"
if [ -n "$ALG" ]; then
  ZRAM_CONTENT="$ZRAM_CONTENT
compression-algorithm = $ALG"
fi

ZRAM_CHANGED=0
SWAPFILE_FAILED=0
write_if_changed "$ZRAM_CONF" "$ZRAM_CONTENT" && ZRAM_CHANGED=1

# 2. /swapfile stays as lower-priority overflow. Re-enable it if it is in
#    fstab but somehow off; never disable it.
if [ -f /swapfile ] && grep -qE '^/swapfile[[:space:]]' /etc/fstab; then
  if swapon --show=NAME --noheadings | grep -qx /swapfile; then
    log "/swapfile active (overflow)"
  elif ! run swapon /swapfile; then
    # Not fatal for zram setup, but the run must report failure (see end).
    echo "[zram] WARNING: could not re-enable /swapfile" >&2
    SWAPFILE_FAILED=1
  fi
fi

# 3. Activate zram0 now (the generator handles every later boot).
activate_zram() {
  systemctl daemon-reload || return 1
  if ! systemctl cat "$SWAP_UNIT" >/dev/null 2>&1; then
    echo "[zram] zram-generator did not create $SWAP_UNIT; check $ZRAM_CONF" >&2
    return 1
  fi
  # Restart (not start) the setup unit so a previously failed or half-done
  # setup is redone; the device is not in use as swap at this point.
  systemctl reset-failed "$SETUP_UNIT" "$SWAP_UNIT" 2>/dev/null || true
  systemctl restart "$SETUP_UNIT" || return 1
  systemctl start "$SWAP_UNIT" || return 1
  zram_swap_active
}

if zram_swap_active; then
  log "$ZRAM_DEV is already an active swap device; not touching it"
  if [ "$ZRAM_CHANGED" = 1 ]; then
    log "note: new zram config takes effect on the next reboot (resizing an in-use swap device would need swapoff)"
  fi
elif [ "$DRY_RUN" = 1 ]; then
  log "dry-run: would run: systemctl daemon-reload; systemctl restart $SETUP_UNIT; systemctl start $SWAP_UNIT"
  log "dry-run: sysctls below are applied only once $ZRAM_DEV is confirmed active"
elif ! activate_zram; then
  # systemctl can report an error even though the device came up; trust the
  # actual swap state. If zram0 isn't active, exit and let on_exit roll the
  # sysctls back.
  if zram_swap_active; then
    echo "[zram] WARNING: activation reported an error, but $ZRAM_DEV is active swap; continuing" >&2
  else
    echo "[zram] ERROR: $ZRAM_DEV did not become an active swap device" >&2
    exit 1
  fi
fi

# 4. Sysctls commonly recommended with zram swap - only reached once
#    /dev/zram0 is confirmed active (or in dry-run):
#  - swappiness 150: swapping anon pages to zram costs a compression, which
#    is cheaper than dropping file cache (binaries, SQLite pages) and
#    re-reading it from EBS.
#  - page-cluster 0: zram has no seek cost, so read-ahead of 8 pages only
#    wastes CPU and memory.
#  - watermark_boost_factor 0 / watermark_scale_factor 125: no reclaim bursts
#    after fragmentation events; kswapd starts a little earlier and runs
#    steadier.
SYSCTL_CONTENT="# Managed by Terraform: infra/modules/compute/zram-swap.sh (SSM association
# glassbox-zram-swap). Hand edits are overwritten. Removed automatically if
# zram fails to activate on a later run.
vm.swappiness = 150
vm.page-cluster = 0
vm.watermark_boost_factor = 0
vm.watermark_scale_factor = 125"
write_if_changed "$SYSCTL_CONF" "$SYSCTL_CONTENT" || true
# Always re-apply (cheap) so a manual `sysctl -w` drift self-heals weekly.
run sysctl -p "$SYSCTL_CONF"

log "--- state"
swapon --show || true
zramctl 2>/dev/null || true
sysctl vm.swappiness vm.page-cluster vm.watermark_boost_factor vm.watermark_scale_factor || true
echo "memory PSI: $(head -2 /proc/pressure/memory | tr '\n' ' ')"
echo "io PSI:     $(head -2 /proc/pressure/io | tr '\n' ' ')"
if [ "$SWAPFILE_FAILED" = 1 ]; then
  echo "[zram] finished with an error: /swapfile could not be re-enabled (zram is active)" >&2
  exit 1
fi
log "done"
}

main "$@"
