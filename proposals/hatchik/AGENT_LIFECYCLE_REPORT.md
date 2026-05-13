# Sandbox Idle-Archive Lifecycle — agent report

Built end-to-end on branch `worktree-agent-a8fea2d6717dbc847` (off
`cranky-nash-5e9af5`). Nothing deployed; artifacts live in the
repository for review.

## Files added

| Path | Purpose |
|---|---|
| `proposals/hatchik/sandbox-orchestrator/lifecycle.py` | Daily reconciler. Warns at day 23/29, archives at day 30, purges at day 37. Idempotent + defensive. |
| `proposals/hatchik/sandbox-orchestrator/restore.py` | Admin CLI: revive an archived sandbox from `/var/hatchik-archive/<slug>/`. |
| `proposals/hatchik/sandbox-orchestrator/hatchik-lifecycle.service` | systemd one-shot service that runs `lifecycle.py`. |
| `proposals/hatchik/sandbox-orchestrator/hatchik-lifecycle.timer` | Fires the above daily at 02:00 UTC with 5min jitter, `Persistent=true`. |
| `proposals/hatchik/sandbox-orchestrator/LIFECYCLE_TESTING.md` | Test procedure using `HATCHIK_LIFECYCLE_FAKE_NOW` + threshold overrides. |
| `proposals/hatchik/restore-sandbox.html` | Customer-facing static page. Mirrors `delete-sandbox.html` style, indigo CTA, optional note field. |
| `proposals/hatchik/AGENT_LIFECYCLE_REPORT.md` | This file. |

## Files modified

| Path | Change |
|---|---|
| `proposals/hatchik/signup-service/main.py` | Added `POST /api/admin/account/{slug}/restore` (X-Admin-Token gated; subprocess-runs `restore.py`). Added `POST /api/account/request-restore` ({email, note}). Adds `RESTORE_SCRIPT` env-var const + a founder-notification email helper. Rate-limited by reusing the existing IP-based limiter. |
| `proposals/hatchik/index.html` | Added "Restore sandbox" link in the footer, just before "Delete sandbox". |
| `proposals/hatchik/FIRST_CUSTOMER_RUNBOOK.md` | New "Idle-archive lifecycle" section with the day table, timer install, manual archive/restore commands, and a "when something goes wrong" subsection. |
| `proposals/hatchik/sandbox-orchestrator/README.md` | Added `lifecycle.py` + `restore.py` to the file table; added a "Lifecycle timer install" section. |

## Design decisions

### Activity detection — Postgres query, not API hits
The user spec is explicit: `max(GREATEST(last_sign_in_at, created_at))`
on `auth.users`. Implemented via `docker exec <slug>-postgres-1 psql
… -tAc "…"`. Falling back to `created_at` means the owner pre-creation
counts as activity, so a sandbox no human has touched still has the
full 30 days from provisioning (not "archive instantly because no
sign_in").

### Idempotency markers in the registry
Sent-email flags live as ISO-timestamp fields on each tenant entry:
`archive_warning_23_at`, `archive_warning_29_at`, `archived_at`,
`purged_at`, `restored_at`. The reconciler only sends each email when
its marker is unset; restore clears the warning markers so the cycle
can re-run cleanly if the sandbox idles again later.

### Sign-in resets the clock — implicit
We don't need a separate "reset" path. Every reconciler run re-queries
Postgres; if `last_sign_in_at` is fresh, `days_idle` drops below the
warning threshold and no email is sent. Belt-and-braces: I added an
explicit `reset-warnings` action that **clears** the warning markers
when `days_idle` drops back below WARN1 — so the next idle stretch
sends them again.

### Volume snapshots, not docker volume save
`docker volume save` doesn't exist. The canonical idiom is a
throwaway alpine container that tars the volume mount to stdout. Used
by both `lifecycle.py` (write) and `restore.py` (read). Volumes are
discovered dynamically with `docker volume ls --filter name=<slug>_`
so the substrate can add new volumes without breaking the snapshot
list.

### Archive preserves the tenant dir too
The substrate's compose+.env+Caddyfile are part of the snapshot
(`tenant-dir.tar.gz`) alongside the volume tarballs and a
`manifest.json`. This means restore doesn't need to re-render from
the substrate template — it just untars the original state. Safer:
substrate-template drift between archive and restore won't break
anyone.

### Day-30 path uses `docker compose stop` first, then `down -v` after
snapshots are written. This sequence means an interrupted archive
(e.g. host reboot) leaves a still-archivable but stopped tenant —
re-running the reconciler picks up where it left off. If we did
`down -v` first we could lose data on a failed snapshot.

### Restore is admin-gated
The user spec called this out and I kept it strict. Archives are
valuable to spammers (account-takeover, free hosting via re-mint of
the owner magic-link). The customer-facing path is "email me",
not a self-serve click. The form just notifies the founder; an admin
must run `restore.py` or hit the admin endpoint to actually restore.

### Time-override env vars
`HATCHIK_LIFECYCLE_FAKE_NOW`, `HATCHIK_LIFECYCLE_WARN1_DAY`,
`HATCHIK_LIFECYCLE_WARN2_DAY`, `HATCHIK_LIFECYCLE_ARCHIVE_DAY`,
`HATCHIK_LIFECYCLE_PURGE_DAYS_AFTER_ARCHIVE`. These all live on
**production code paths** — no separate test mode. Testing exercises
the same code that customers do. See `LIFECYCLE_TESTING.md`.

### Defensive — per-tenant try/except
Each tenant is reconciled inside its own `try/except`. A crashed
postgres probe, a Resend timeout, a corrupted registry entry — none
of these can block other tenants. The exception is logged with
`log.exception` so the traceback hits journalctl.

### No new dependencies
`lifecycle.py` and `restore.py` import `httpx` (already used) and
stdlib only. They reuse `provision.py` helpers (`_load_env_file`,
`supabase_jwt`, `wait_for_tenant_health`, `write_tenant_caddy_route`)
via a `sys.path` insert — keeps the env loader + JWT signing in one
place.

### Email design consistency
All four templates use the existing brand shell (beige #f6f5f1
background, white card, indigo #4f46e5 CTAs, "Hi {first_name},"
greeting, no-reply footer). British English ("colour", "tyres",
"organisation" elsewhere; here "behaviour", "centre"). No apologies.
No jargon.

## Email copy summary

| Email | Subject | Tone | Magic-link? |
|---|---|---|---|
| Day-23 warning | "Your <product> sandbox is heading for archive" | Polite, soft, "sign in if you want to keep building" | Yes |
| Day-29 reminder | "Tomorrow: your <product> sandbox archives" | Firmer, "last chance" | Yes (fresh) |
| Archival notice | "Your <product> sandbox has been archived" | Reassuring, "data safe for 7 days", restore link | No (it's down) |
| Restore success | "Your <product> sandbox is back" | Cheerful, sign-in CTA | Yes |
| Final purge | "Your <product> sandbox has been deleted" | Brief, "sign up again any time" | No |

## Registry shape after lifecycle

Live tenant (post-day-23 warning):

```json
{
  "prepsheet": {
    "slug": "prepsheet",
    "port": 18000,
    "email": "alice@example.com",
    "product_name": "PrepSheet",
    "signup_id": 12,
    "status": "live",
    "created_at": 1714752000,
    "url": "https://prepsheet.hatchik.com",
    "archive_warning_23_at": "2026-05-13T02:00:11+00:00"
  }
}
```

Archived tenant:

```json
{
  "prepsheet": {
    "slug": "prepsheet",
    "port": 18000,
    "email": "alice@example.com",
    "product_name": "PrepSheet",
    "signup_id": 12,
    "status": "archived",
    "created_at": 1714752000,
    "url": "https://prepsheet.hatchik.com",
    "archive_warning_23_at": "2026-05-13T02:00:11+00:00",
    "archive_warning_29_at": "2026-05-19T02:00:08+00:00",
    "archived_at": "2026-05-20T02:00:14+00:00"
  }
}
```

Restored:

```json
{
  "prepsheet": {
    ...
    "status": "live",
    "restored_at": "2026-05-22T14:30:00+00:00"
    // archived_at, archive_warning_*_at fields removed
  }
}
```

## Open questions

1. **Magic-link minting fails if tenant Postgres is down**. Day-23 +
   day-29 emails currently embed a fresh magic-link; if the GoTrue
   admin API is unreachable, the email falls back to the bare URL
   ("Open your sandbox: https://…") which forces the customer through
   the signup flow. If we want to guarantee a click-through link in
   the warning emails even when the tenant is sick, we'd need a
   signup-service-issued bridging token. Not blocking launch.

2. **Activity probe latency**. `docker exec` of `psql` takes ~1–2s
   per tenant. With 10 tenants the reconciler runs in ~30s. Fine for
   daily. If we scale to 100 tenants on multi-host, we'd want to query
   each tenant's Postgres via TCP from a single reconciler process
   instead of forking docker exec per tenant.

3. **`docker compose stop` doesn't guarantee Postgres has flushed**.
   In practice Postgres does a clean shutdown when SIGTERM'd by
   compose. To be paranoid we could `pg_basebackup` before stopping
   — but the volume snapshot captures the on-disk WAL and Postgres
   recovers cleanly on restore. Tested mentally; would smoke-test
   per `LIFECYCLE_TESTING.md` §2 before claiming "GA".

4. **Restore + port collision**. The original port is preserved in
   the manifest. If a new tenant claimed the freed port during
   archive (the registry still has the port assignment though — so
   the allocator should skip it), compose-up will fail with EADDRINUSE.
   We don't auto-pick a new port; we surface the failure to the admin.
   Acceptable for the current scale (≤10 tenants).

5. **No archive size cap**. Archives live forever (well, 7 days, but
   `/var/hatchik-archive` could fill up if a flood of customers
   archives at once). The host has ~80GB free; at ~5GB per sandbox
   that's 16 simultaneous archives. Beyond that we'd want a disk
   alarm in monitoring (out of scope here).

6. **No copy on hatchik.com about the 7-day grace period in the
   archive email itself**. The customer-facing language on the
   marketing page just says "archived if idle 30 days". The archival
   notice email I wrote mentions the 7-day grace period and the
   restore form — the customer learns about it when they hit it. May
   want to surface this in the FAQ; out of scope here.

## How to validate (without deploying)

```bash
# Syntax
python3 -c "import ast; [ast.parse(open(p).read()) for p in [
  'proposals/hatchik/sandbox-orchestrator/lifecycle.py',
  'proposals/hatchik/sandbox-orchestrator/restore.py',
  'proposals/hatchik/signup-service/main.py',
]]"

# HTML well-formed
python3 -c "import html.parser; html.parser.HTMLParser().feed(open('proposals/hatchik/restore-sandbox.html').read())"
```

End-to-end testing requires the sandbox host (Docker + per-tenant
Postgres + GoTrue) — see `LIFECYCLE_TESTING.md`.
