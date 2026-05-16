# Lifecycle reconciler — testing guide

`lifecycle.py` walks every live tenant through a 30+7 day clock. Real
calendar testing takes 37 days. These tricks collapse it to under a
minute.

All test paths use the **same code** that production runs — the only
overrides are the day thresholds + a fake "now". No alternate code
paths.

## 1. Dry-run on the real registry

Safest first check. Doesn't change anything; just prints decisions.

```bash
# On the sandbox host
python3 /opt/hatchik-orchestrator/lifecycle.py --dry-run --json | jq
```

Output shape:

```json
{
  "now": "2026-05-13T11:32:00+00:00",
  "dry_run": true,
  "tenants": [
    {
      "slug": "prepsheet",
      "status": "live",
      "days_idle": 4.12,
      "last_activity": "2026-05-09T08:12:33+00:00",
      "actions": ["no-action"]
    },
    {
      "slug": "demo",
      "status": "archived",
      "days_since_archive": 2.04,
      "actions": ["archive-grace-period"]
    }
  ]
}
```

## 2. Walk a single tenant through the full cycle

Pick a disposable tenant — ideally one you just provisioned for this
test:

```bash
python3 /opt/hatchik-orchestrator/provision.py \
  --slug lifecycletest --email you@hatchik.com --product "Lifecycle Test"
```

The provision puts `created_at` = now in the registry; `auth.users`
has the owner row also created at now. So all activity timestamps
agree on T0.

### 2a. Force day-23 warning

```bash
# 23 days after provisioning
FAKE=$(date -u -d "+23 days" +%Y-%m-%dT%H:%M:%SZ)
HATCHIK_LIFECYCLE_FAKE_NOW=$FAKE \
  python3 /opt/hatchik-orchestrator/lifecycle.py --slug lifecycletest --json
```

Expected: `actions: ["warn23-sent"]`, registry now has
`archive_warning_23_at`. Your inbox has the polite email.

Re-run the same command — it should produce `actions: ["no-action"]`
because the warning marker is already set.

### 2b. Force day-29 reminder

```bash
FAKE=$(date -u -d "+29 days" +%Y-%m-%dT%H:%M:%SZ)
HATCHIK_LIFECYCLE_FAKE_NOW=$FAKE \
  python3 /opt/hatchik-orchestrator/lifecycle.py --slug lifecycletest --json
```

Expected: `actions: ["warn29-sent"]`, registry has
`archive_warning_29_at`. Inbox has the firmer email.

### 2c. Force archive (day 30)

```bash
FAKE=$(date -u -d "+30 days" +%Y-%m-%dT%H:%M:%SZ)
HATCHIK_LIFECYCLE_FAKE_NOW=$FAKE \
  python3 /opt/hatchik-orchestrator/lifecycle.py --slug lifecycletest --json
```

Expected:

- Tenant's docker compose is stopped + removed (`docker ps | grep
  lifecycletest` returns nothing)
- `/var/hatchik-archive/lifecycletest/` exists with `manifest.json` +
  `*.tar.gz` volume snapshots + `tenant-dir.tar.gz`
- `/opt/hatchik-tenants/lifecycletest/` is **gone** (everything you
  need is in the archive)
- `/opt/hatchik-host-caddy/tenants.d/lifecycletest.caddy` is gone
- Registry: `status=archived`, `archived_at=<FAKE>`
- Inbox: "Your sandbox has been archived" with restore link

`curl -sk https://lifecycletest.hatchik.com/` should now return a host
Caddy 404 (no route).

### 2d. Restore from archive

```bash
python3 /opt/hatchik-orchestrator/restore.py lifecycletest --json
```

Expected:

- Tenant dir back in `/opt/hatchik-tenants/lifecycletest/`
- Volumes recreated and populated
- Compose stack up + healthy (`docker compose ps` shows all green)
- Caddy route written + reloaded
- Registry: `status=live`, `archived_at` cleared, `restored_at` set,
  warning markers cleared
- Inbox: "Your sandbox is back" with fresh magic-link
- `curl -sk https://lifecycletest.hatchik.com/` returns 200

### 2e. Force purge (day 30+7 since we faked day 30)

You're now in `status=archived` with `archived_at` set to the day-30
FAKE timestamp. To trigger purge, fake "now" to day 37 of the
original timeline:

```bash
# But wait — you just restored, so the tenant is back to status=live.
# To test purge, archive it again first (re-run 2c), then jump forward 7 days.
```

Or, easier: skip 2d, and from 2c's state:

```bash
FAKE=$(date -u -d "+37 days" +%Y-%m-%dT%H:%M:%SZ)
HATCHIK_LIFECYCLE_FAKE_NOW=$FAKE \
  python3 /opt/hatchik-orchestrator/lifecycle.py --slug lifecycletest --json
```

Expected:

- `/var/hatchik-archive/lifecycletest/` is gone
- Registry: `status=purged`, `purged_at=<FAKE>`
- Signup row `status=archived_purged` (check via
  `sqlite3 /var/lib/hatchik/signups.db "SELECT id,email,status FROM signups WHERE id=<n>"`)
- Inbox: "Your sandbox has been deleted"

## 3. Walking the whole cycle in 30 seconds (collapsed thresholds)

If you want to test the *behaviour* without faking 30 calendar days at
each step, collapse the thresholds:

```bash
# Day 23 → 0 minutes idle, etc. Just exercises the email-once logic
# and the state transitions; doesn't help if you actually need to
# verify the snapshot/restore round-trip with hours of accumulated data.
HATCHIK_LIFECYCLE_WARN1_DAY=0 \
HATCHIK_LIFECYCLE_WARN2_DAY=0 \
HATCHIK_LIFECYCLE_ARCHIVE_DAY=0 \
HATCHIK_LIFECYCLE_PURGE_DAYS_AFTER_ARCHIVE=0 \
  python3 /opt/hatchik-orchestrator/lifecycle.py --slug lifecycletest --json
```

Caveat: with `WARN1_DAY=0` the warning fires immediately; with
`ARCHIVE_DAY=0` it also archives immediately in the same run (archive
is checked first). For granular walks, raise each threshold slightly
between runs (e.g. WARN1=0 first, then ARCHIVE=0 second).

## 4. Sign-in resets the clock

After 2a (day-23 warning sent), sign into the sandbox via the magic-
link in the email. Then re-run with a fake "now" earlier than day 23
(or just now without faking):

```bash
python3 /opt/hatchik-orchestrator/lifecycle.py --slug lifecycletest --json
```

Expected: `actions: ["reset-warnings"]`, `archive_warning_23_at` gone
from the registry, `days_idle` near zero.

## 5. Defensive behaviour — container down

Stop the tenant's postgres container manually:

```bash
docker stop lifecycletest-postgres-1
```

Re-run the reconciler:

```bash
python3 /opt/hatchik-orchestrator/lifecycle.py --slug lifecycletest --json
```

Expected: a WARN log line like
`activity probe for lifecycletest timed out/failed`, and the reconciler
falls back to `registry.created_at`. Other tenants are not affected —
the per-tenant try/except catches any crash so one sick container
can't block the reconciler.

## 6. Customer restore request flow

Submit the form at `https://hatchik.com/restore-sandbox` with the
archived tenant's signup email. Founder inbox should receive an email
with subject `[Hatchik] Restore request — <email>` containing the
matching slug(s). Anti-enumeration: the same UI success message shows
whether or not an archive exists.

## Cleanup after testing

```bash
python3 /opt/hatchik-orchestrator/decommission.py lifecycletest --hard
rm -rf /var/hatchik-archive/lifecycletest
```

## Things to *not* test in production

- Don't `HATCHIK_LIFECYCLE_FAKE_NOW=` real customer tenants. The
  warning emails go straight to the customer's inbox.
- Don't run the reconciler with `WARN1_DAY=0 ARCHIVE_DAY=0` against
  the real registry — it will archive everyone in one pass.
- Restrict testing to slugs you provisioned for the test (`--slug`
  flag).
