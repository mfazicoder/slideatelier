#!/usr/bin/env bash
#
# check_overcommit.sh — read-only diagnostic for the Hatchik overcommit setup.
#
# Prints current sysctl, swap, and PSI memory state. Safe to run as any user
# (some readings degrade gracefully without root). No modifications.

set -u

hr() { printf -- '---- %s ----\n' "$*"; }

hr "sysctl: vm.overcommit_memory (expect 1)"
sysctl vm.overcommit_memory 2>/dev/null || echo "(could not read)"

hr "sysctl: vm.swappiness (expect 10)"
sysctl vm.swappiness 2>/dev/null || echo "(could not read)"

hr "sysctl: vm.vfs_cache_pressure (expect 50)"
sysctl vm.vfs_cache_pressure 2>/dev/null || echo "(could not read)"

hr "drop-in present?"
DROPIN=/etc/sysctl.d/99-hatchik-overcommit.conf
if [[ -f $DROPIN ]]; then
  echo "$DROPIN exists:"
  cat "$DROPIN"
else
  echo "MISSING: $DROPIN (setup_overcommit.sh has not been run)"
fi

hr "backup of original sysctl.conf"
if [[ -f /etc/sysctl.conf.pre-hatchik ]]; then
  echo "OK: /etc/sysctl.conf.pre-hatchik exists ($(stat -c %s /etc/sysctl.conf.pre-hatchik 2>/dev/null || stat -f %z /etc/sysctl.conf.pre-hatchik) bytes)"
else
  echo "MISSING: /etc/sysctl.conf.pre-hatchik"
fi

hr "swap (expect /swapfile, ~16G)"
swapon --show 2>/dev/null || echo "(swapon --show failed)"

hr "free -h"
free -h 2>/dev/null || echo "(free not available)"

hr "/etc/fstab swap entries"
grep -E '(^|[^#])swap' /etc/fstab 2>/dev/null || echo "(no swap entry found in /etc/fstab)"

hr "/proc/pressure/memory (avg10 of 'some' should be < 20 in steady state)"
if [[ -r /proc/pressure/memory ]]; then
  cat /proc/pressure/memory
else
  echo "(unavailable — kernel < 4.20 or PSI not enabled)"
fi

hr "transparent hugepages (expect [madvise] or [never])"
THP=/sys/kernel/mm/transparent_hugepage/enabled
if [[ -r $THP ]]; then
  cat "$THP"
else
  echo "(THP sysfs not readable)"
fi

hr "watchdog timer"
if command -v systemctl >/dev/null 2>&1; then
  systemctl is-active hatchik-memory-watchdog.timer 2>/dev/null \
    && echo "(timer is active)" \
    || echo "(timer not active — see runbook section 8)"
else
  echo "(no systemctl)"
fi

hr "recent host-level OOM (last hour)"
if command -v journalctl >/dev/null 2>&1; then
  journalctl -k --since '1 hour ago' 2>/dev/null | grep -i 'out of memory' | tail -5 \
    || echo "(none)"
else
  echo "(no journalctl available)"
fi

echo
echo "done."
