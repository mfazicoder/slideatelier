#!/usr/bin/env bash
# hatchik-restore.sh — list snapshots / restore a specific snapshot.
#
# Usage:
#   hatchik-restore.sh list                      # list available snapshots
#   hatchik-restore.sh restore <snapshot-id> <target-dir>
#                                                # restore into target-dir
#                                                # (does NOT touch live data)
#
# After restoring you decide which files to copy back to /var/lib/hatchik/,
# /opt/hatchik-orchestrator/, etc. Restore script never overwrites live.

set -euo pipefail
set -a; . /etc/hatchik-backup.env; set +a

case "${1:-}" in
  list)   restic snapshots --compact ;;
  restore)
    snap="${2:?usage: hatchik-restore.sh restore <snapshot-id> <target-dir>}"
    target="${3:?usage: hatchik-restore.sh restore <snapshot-id> <target-dir>}"
    mkdir -p "$target"
    restic restore "$snap" --target "$target"
    echo ""
    echo "Restored into $target. Live data was not touched."
    echo "Inspect, then manually copy what you need (signups.db, registries, etc.)."
    ;;
  *) echo "Usage: hatchik-restore.sh {list | restore <snap-id> <target>}" >&2; exit 1 ;;
esac
