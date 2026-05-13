#!/usr/bin/env bash
#
# hatchik-backup.sh — nightly backup of the Hatchik sandbox host.
#
# What it backs up:
#   - /var/lib/hatchik/signups.db          (SQLite, atomic .backup)
#   - /opt/hatchik-tenants/registry.json
#   - /opt/hatchik-launch-orchestrator/registry.json
#   - /var/lib/docker/volumes/hatchik-host-caddy_caddy_data/  (TLS certs)
#   - pg_dump for every running tenant Postgres container
#
# Backend: restic. Repository configured via RESTIC_REPOSITORY in
# /etc/hatchik-backup.env. Defaults to /var/backups/hatchik/repo (local).
# Flip to Backblaze B2 by editing the env file (no script change needed).
#
# Retention: --keep-daily 14 --keep-weekly 4. Tweak in the forget step.
#
# Restore: see /usr/local/bin/hatchik-restore.sh

set -euo pipefail

# Load encrypted-repo credentials
if [ ! -r /etc/hatchik-backup.env ]; then
  echo "FATAL: /etc/hatchik-backup.env missing — cannot back up" >&2
  exit 2
fi
set -a; . /etc/hatchik-backup.env; set +a

STAGING=$(mktemp -d /tmp/hatchik-backup.XXXXXX)
trap "rm -rf $STAGING" EXIT

echo "[$(date -u +%F\ %T)] backup starting; staging=$STAGING"

# ─── 1. SQLite (atomic snapshot) ────────────────────────────────────
if [ -r /var/lib/hatchik/signups.db ]; then
  echo "  sqlite: signups.db"
  sqlite3 /var/lib/hatchik/signups.db ".backup $STAGING/signups.db"
  # sanity: was the snapshot at least as big as the live file?
  ls -lh "$STAGING/signups.db" 2>&1 | awk "{print \"    size: \" \$5}"
fi

# ─── 2. Registries ─────────────────────────────────────────────────
for r in /opt/hatchik-tenants/registry.json \
         /opt/hatchik-launch-orchestrator/registry.json; do
  if [ -r "$r" ]; then
    cp "$r" "$STAGING/$(basename $(dirname "$r"))-registry.json"
    echo "  registry: $r"
  fi
done

# ─── 3. Caddy TLS state ────────────────────────────────────────────
CADDY_VOL=/var/lib/docker/volumes/hatchik-host-caddy_caddy_data
if [ -d "$CADDY_VOL" ]; then
  echo "  caddy: $CADDY_VOL"
  tar -C "$CADDY_VOL" -czf "$STAGING/caddy-data.tar.gz" .
fi

# ─── 4. Tenant Postgres dumps ─────────────────────────────────────
TENANTS_DIR=/opt/hatchik-tenants
mkdir -p "$STAGING/tenants"
if [ -d "$TENANTS_DIR" ]; then
  for tenant_dir in "$TENANTS_DIR"/*/; do
    [ -d "$tenant_dir" ] || continue
    slug=$(basename "$tenant_dir")
    # Find the running postgres container for this tenant
    container=$(docker ps --format "{{.Names}}" | grep -E "^${slug}-postgres" | head -1 || true)
    if [ -z "$container" ]; then
      echo "  skip $slug (no postgres container running)"
      continue
    fi
    echo "  pg_dump: $slug ($container)"
    docker exec "$container" pg_dump -U postgres -Fc -Z 6 postgres \
      > "$STAGING/tenants/${slug}.dump.gz" 2>/dev/null || {
        echo "    WARN: pg_dump failed for $slug — continuing"
        rm -f "$STAGING/tenants/${slug}.dump.gz"
      }
  done
fi

# ─── 5. Restic backup ─────────────────────────────────────────────
echo "[$(date -u +%F\ %T)] running restic backup"
restic backup "$STAGING" \
  --tag "hatchik-host" \
  --tag "$(date -u +%F)" \
  --host hatchik-sandbox-host

# ─── 6. Retention ─────────────────────────────────────────────────
echo "[$(date -u +%F\ %T)] pruning old snapshots"
restic forget --keep-daily 14 --keep-weekly 4 --prune

echo "[$(date -u +%F\ %T)] backup complete"
