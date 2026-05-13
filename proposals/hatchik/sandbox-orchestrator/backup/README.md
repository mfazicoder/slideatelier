# Hatchik backups

Nightly encrypted backup of everything the sandbox host can't trivially
re-create: the signups DB, both orchestrator registries, the host Caddy
TLS state, and a `pg_dump` per running tenant Postgres.

## Layout

| File | Purpose |
|---|---|
| `hatchik-backup.sh` | The backup script. Runs at 02:30 UTC nightly. Stages everything in `/tmp/hatchik-backup.XXXXXX/` then hands off to `restic backup`. |
| `hatchik-restore.sh` | `list` or `restore <snap-id> <target-dir>`. Never overwrites live data — restores into a fresh target dir for the operator to pick from. |
| `hatchik-backup.service` | systemd one-shot unit that runs the backup script. |
| `hatchik-backup.timer` | Daily 02:30 UTC trigger with 10-min jitter. |

## Install on a host

```bash
install -m 755 hatchik-backup.sh    /usr/local/bin/
install -m 755 hatchik-restore.sh   /usr/local/bin/
install -m 644 hatchik-backup.service /etc/systemd/system/
install -m 644 hatchik-backup.timer   /etc/systemd/system/

# Generate the encryption passphrase + write the env file
PASS=$(openssl rand -base64 48 | tr -d '\n')
cat > /etc/hatchik-backup.env <<EOF
RESTIC_REPOSITORY=/var/backups/hatchik/repo
RESTIC_PASSWORD=${PASS}
EOF
chmod 600 /etc/hatchik-backup.env

# Init the local restic repo
mkdir -p /var/backups/hatchik && chmod 700 /var/backups/hatchik
set -a; . /etc/hatchik-backup.env; set +a
restic init

# Enable the timer
systemctl daemon-reload
systemctl enable --now hatchik-backup.timer

# Smoke-test
/usr/local/bin/hatchik-backup.sh
/usr/local/bin/hatchik-restore.sh list
```

## Off-site (Backblaze B2) — the upgrade

Right now backups live on the same disk as the data they protect. That
covers DB corruption + accidental rm, but not "the whole VPS got nuked
or stolen." Flip to off-site B2 by editing `/etc/hatchik-backup.env`:

```bash
RESTIC_REPOSITORY=b2:hatchik-backups:/sandbox-host
B2_ACCOUNT_ID=<your-key-id>
B2_ACCOUNT_KEY=<your-application-key>
```

Then `restic init` to create the remote repo (fresh — won't migrate the
local one). After that, every nightly run pushes to B2 instead of local.

**Sizing & cost:** total daily snapshot size today is ~115 KiB
(signups DB 108K + registries 4K + Caddy data 88K). Per-tenant Postgres
dumps add ~5-15 MB each (Supabase substrate baseline). At 100 tenants
the daily delta is maybe 50 MB; restic dedup pulls it well below that.
Backblaze B2 at $0.005/GB/month makes the storage bill negligible (cents).

The `keep 14 daily / 4 weekly` retention policy in the script applies
identically against any backend — no changes needed when switching.

## Restore drill

Periodically (suggested: monthly) run a restore drill to a scratch dir:

```bash
hatchik-restore.sh list                               # latest snap id
hatchik-restore.sh restore <id> /tmp/restore-drill   # pull to scratch
sqlite3 /tmp/restore-drill/.../signups.db "SELECT COUNT(*) FROM signups;"
```

If the count matches what `/var/lib/hatchik/signups.db` currently has
(or what you expect from the snapshot date), the drill passes.

## What this does NOT cover

- **Host config drift**: /etc, /opt orchestrator code, systemd units.
  Lives in git already (this repo). Re-deploy from git is the recovery
  path for code; the backup is for state.
- **Per-tenant uploaded files** (Supabase storage volumes): on the
  roadmap. Currently relies on each tenant's own backups if any.
- **Hetzner snapshot**: not used. Restic gives finer-grained restore
  + works across providers; Hetzner snapshots are a separate concern.
