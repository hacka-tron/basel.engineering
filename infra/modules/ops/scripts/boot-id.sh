# glassbox-ops-boot-id: prints this boot's ID and boot time, one parseable
# line each. Read-only; reboot-node compares it before and after a reboot.

main() {
  exec </dev/null 2>&1
  printf 'boot_id=%s\n' "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)"
  printf 'boot_epoch=%s\n' "$(awk '$1 == "btime" { print $2 }' /proc/stat 2>/dev/null || echo 0)"
}
main "$@"
