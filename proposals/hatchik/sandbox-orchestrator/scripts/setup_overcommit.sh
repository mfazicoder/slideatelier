#!/usr/bin/env bash
#
# setup_overcommit.sh — one-time host setup for Hatchik sandbox memory overcommit.
#
# Idempotent: safe to re-run. Each step checks for prior completion before acting.
# See sandbox-orchestrator/runbooks/memory-overcommit.md for the rationale.
#
# Requires: root (uses sysctl, edits /etc/sysctl.d, creates /swapfile, edits /etc/fstab).
# Target:   Hetzner CAX31 (NVMe root disk) running Hatchik sandbox tenants ONLY.

set -euo pipefail

SYSCTL_BACKUP=/etc/sysctl.conf.pre-hatchik
SYSCTL_DROPIN=/etc/sysctl.d/99-hatchik-overcommit.conf
SWAPFILE=/swapfile
SWAPSIZE_MB=16384  # 16 GB
FSTAB=/etc/fstab

log()  { printf '[setup_overcommit] %s\n' "$*"; }
fail() { printf '[setup_overcommit][FATAL] %s\n' "$*" >&2; exit 1; }

# --- preflight ---------------------------------------------------------------

if [[ $EUID -ne 0 ]]; then
  fail "must run as root (try: sudo $0)"
fi

if [[ ! -d /etc/sysctl.d ]]; then
  fail "/etc/sysctl.d missing — this script targets systemd hosts only"
fi

# --- 1. backup /etc/sysctl.conf (skip if already backed up) ------------------

if [[ -f $SYSCTL_BACKUP ]]; then
  log "backup already exists at $SYSCTL_BACKUP — skipping"
else
  if [[ -f /etc/sysctl.conf ]]; then
    cp /etc/sysctl.conf "$SYSCTL_BACKUP" || fail "could not back up /etc/sysctl.conf"
    log "backed up /etc/sysctl.conf -> $SYSCTL_BACKUP"
  else
    # touch an empty marker so re-runs still skip
    : > "$SYSCTL_BACKUP" || fail "could not create $SYSCTL_BACKUP marker"
    log "no /etc/sysctl.conf to back up; created empty marker $SYSCTL_BACKUP"
  fi
fi

# --- 2. drop in hatchik sysctl settings -------------------------------------

DROPIN_CONTENT="# Managed by sandbox-orchestrator/scripts/setup_overcommit.sh
# See runbooks/memory-overcommit.md for rationale. Do not edit by hand.
vm.overcommit_memory = 1
vm.swappiness = 10
vm.vfs_cache_pressure = 50
"

if [[ -f $SYSCTL_DROPIN ]] && [[ "$(cat "$SYSCTL_DROPIN")" == "$DROPIN_CONTENT" ]]; then
  log "$SYSCTL_DROPIN already up to date — skipping"
else
  printf '%s' "$DROPIN_CONTENT" > "$SYSCTL_DROPIN" || fail "could not write $SYSCTL_DROPIN"
  log "wrote $SYSCTL_DROPIN"
fi

# --- 3. apply sysctl ---------------------------------------------------------

if ! sysctl --system >/dev/null; then
  fail "sysctl --system failed; check $SYSCTL_DROPIN syntax"
fi
log "applied sysctl --system"

# --- 4. swap file ------------------------------------------------------------

# 4a. allocate (fallocate first; fall back to dd if filesystem rejects fallocate)
if [[ -f $SWAPFILE ]]; then
  log "$SWAPFILE already exists — skipping allocation"
else
  log "allocating $SWAPSIZE_MB MB swap at $SWAPFILE"
  if ! fallocate -l "${SWAPSIZE_MB}M" "$SWAPFILE" 2>/dev/null; then
    log "fallocate failed (likely non-extent filesystem); falling back to dd"
    dd if=/dev/zero of="$SWAPFILE" bs=1M count="$SWAPSIZE_MB" status=progress \
      || fail "dd failed to create $SWAPFILE"
  fi
fi

# 4b. permissions
chmod 600 "$SWAPFILE" || fail "chmod 600 $SWAPFILE failed"

# 4c. mkswap (only if not already formatted as swap)
if ! blkid "$SWAPFILE" 2>/dev/null | grep -q 'TYPE="swap"'; then
  mkswap "$SWAPFILE" >/dev/null || fail "mkswap $SWAPFILE failed"
  log "formatted $SWAPFILE as swap"
else
  log "$SWAPFILE already formatted as swap — skipping mkswap"
fi

# 4d. swapon (only if not already active)
if swapon --show=NAME --noheadings | grep -qx "$SWAPFILE"; then
  log "$SWAPFILE already active — skipping swapon"
else
  swapon "$SWAPFILE" || fail "swapon $SWAPFILE failed"
  log "activated $SWAPFILE"
fi

# 4e. persist in /etc/fstab (noatime,discard per runbook)
FSTAB_LINE="$SWAPFILE none swap sw,noatime,discard 0 0"
if grep -qE "^[^#]*$SWAPFILE[[:space:]]" "$FSTAB"; then
  log "$SWAPFILE already in $FSTAB — skipping"
else
  printf '\n# Hatchik sandbox swap — managed by setup_overcommit.sh\n%s\n' \
    "$FSTAB_LINE" >> "$FSTAB" || fail "could not append to $FSTAB"
  log "added $SWAPFILE to $FSTAB"
fi

# --- 5. verification block --------------------------------------------------

echo
echo "==================== verification ===================="
echo "-- vm.overcommit_memory (expect 1) --"
sysctl vm.overcommit_memory
echo "-- vm.swappiness (expect 10) --"
sysctl vm.swappiness
echo "-- vm.vfs_cache_pressure (expect 50) --"
sysctl vm.vfs_cache_pressure
echo
echo "-- free -h (expect ~16G swap) --"
free -h
echo
echo "-- swapon --show --"
swapon --show
echo
echo "-- /proc/pressure/memory --"
if [[ -r /proc/pressure/memory ]]; then
  cat /proc/pressure/memory
else
  echo "(unavailable — kernel < 4.20 or PSI not enabled)"
fi
echo "======================================================"
echo
log "setup complete. Next: install the watchdog timer (see runbook section 8)."
