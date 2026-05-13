# Hatchik abuse-protection stack — implementation report

Branch: `claude/cranky-nash-5e9af5`
Worktree: `.claude/worktrees/agent-ab5a2170c832534c8/`
Scope: five abuse-mitigation subsystems wired into the existing signup
pipeline. All subsystems degrade gracefully when their env vars aren't
set, so the codebase still runs end-to-end in dev without Turnstile
keys, geo-IP service, or admin tokens.

## Files touched

| Path | Change |
|---|---|
| `proposals/hatchik/signup-service/main.py` | Disposable-email block-list, Turnstile verify, geo-IP lookup, concurrent-provision throttle + queue, schema migration for new columns, admin geo endpoint, queue-delay messaging in customer acknowledgement |
| `proposals/hatchik/substrate-template/docker-compose.yml` | `mem_limit` + `cpus` on every service, parameterised via `${SANDBOX_MEM_*}` / `${SANDBOX_CPUS_*}` with sandbox-sized defaults |
| `proposals/hatchik/start.html` | Turnstile script tag + widget div + token in submit body + 422 error handling |
| `proposals/hatchik/index.html` | Same Turnstile wiring for the inline marketing-page form |
| `proposals/hatchik/account.html` | Turnstile widget on the magic-link sign-in form + 422 surfacing |
| `proposals/hatchik/FIRST_CUSTOMER_RUNBOOK.md` | New "Host capacity per sandbox" section with the per-tenant RAM/CPU table and 4-5 concurrent limit |
| `proposals/hatchik/AGENT_ABUSE_REPORT.md` | This file |

`proposals/hatchik/sandbox-orchestrator/provision.py` was inspected but
not changed — it already doesn't set any `SANDBOX_MEM_*` env vars when
rendering tenant `.env`s, so sandbox tenants inherit the compose
defaults automatically (which is what the brief asked for).

## Env vars added

| Var | Default | Purpose |
|---|---|---|
| `HATCHIK_MAX_CONCURRENT_PROVISIONS` | `3` | Cap on in-flight `provision.py` subprocesses. Excess signups queue in SQLite. |
| `HATCHIK_TURNSTILE_SECRET` | `""` | Cloudflare Turnstile server-side secret. Empty disables verification (dev mode) — `verify_turnstile` returns True and logs a warning. |
| `HATCHIK_GEO_IP_URL` | `https://ipapi.co/{ip}/json/` | Geo-IP lookup endpoint template. Override for testing or to switch providers. |
| `HATCHIK_GEO_IP_DISABLED` | `""` | Set to `1`/`true`/`yes` to skip geo-IP lookups (CI, offline dev). |
| `HATCHIK_BLOCKED_COUNTRIES` | `""` | Comma-separated ISO codes (e.g. `RU,KP`). Empty = no countries blocked. |
| `SANDBOX_MEM_POSTGRES` | `512m` | Postgres RAM cap — overridable per tenant. |
| `SANDBOX_MEM_SUPABASE_AUTH` | `128m` | GoTrue RAM. |
| `SANDBOX_MEM_SUPABASE_REST` | `128m` | PostgREST RAM. |
| `SANDBOX_MEM_SUPABASE_STORAGE` | `128m` | Supabase Storage RAM. |
| `SANDBOX_MEM_SUPABASE_META` | `128m` | postgres-meta RAM. |
| `SANDBOX_MEM_SUPABASE_STUDIO` | `256m` | Studio RAM. |
| `SANDBOX_MEM_API` | `256m` | FastAPI backend RAM. |
| `SANDBOX_MEM_WEB` | `512m` | Vite dev-server RAM. |
| `SANDBOX_MEM_CADDY` | `64m` | Tenant Caddy RAM. |
| `SANDBOX_CPUS_*` | various | Matching CPU-share caps (see compose file). |

## New SQLite columns (additive migration on `signups`)

| Column | Purpose |
|---|---|
| `provision_started_at` (TEXT, ISO-8601 UTC) | Set when `provision.py` is dispatched. Lets us measure queue → start latency later. |
| `provision_finished_at` (TEXT, ISO-8601 UTC) | Set when the subprocess returns. Combined with `provision_started_at` gives a per-tenant boot histogram. |
| `country_code` (TEXT) | From geo-IP lookup, upper-case ISO-3166. NULL if unknown/disabled. |
| `city` (TEXT) | Best-effort city name from geo-IP. NULL on lookup failure. |
| `asn` (TEXT) | ASN string from geo-IP — useful for spotting datacentre/VPN ASNs in the founder notification. |

`init_db()` checks `PRAGMA table_info(signups)` and runs each
`ALTER TABLE ADD COLUMN` only when the column is missing, so re-running
on a live DB is a no-op.

## Status flow (signups.status)

```
new ──┬─► provisioning ─► live      (capacity available, success)
      │                  ╲► failed   (subprocess returned non-zero)
      └─► queued ─► provisioning ─► live/failed
                  ▲
       background worker (5s poll) flips queued → provisioning
       once `len(_in_flight_signups) < MAX_CONCURRENT_PROVISIONS`
```

The worker is started inside the FastAPI `lifespan` context and
cancelled on shutdown so uvicorn restarts don't leak tasks.

## Design decisions

1. **Inline disposable-email list, not a runtime fetch.** ~180 curated
   domains baked into `main.py` as a `frozenset`. Avoids a runtime
   dependency on a domain-list API and keeps the check at O(1). Worth
   re-baking from the GitHub disposable-email-domains list every quarter.

2. **In-memory semaphore + SQLite-persisted queue.** The in-flight set
   gives O(1) "are we at the cap?" reads inside the request handler (to
   pick the queue-delay email message); the SQLite `status='queued'`
   field is the durable queue that survives uvicorn restarts. The
   background worker reconciles the two every 5 seconds.

3. **Turnstile fails open in dev, closed in prod.** `verify_turnstile`
   returns True (with a warning log) when `HATCHIK_TURNSTILE_SECRET` is
   unset. The request handler checks `if TURNSTILE_SECRET and not
   await verify_turnstile(...)` so a configured-but-failing token is a
   hard reject. This means the same code path runs in dev without
   pre-setup, and in prod without manual override.

4. **Geo-IP is best-effort, never blocking.** 3-second `httpx` timeout,
   any HTTP/JSON failure returns an empty geo dict. Local/private IPs
   short-circuit to empty (avoids leaking dev IPs to the third-party
   service). The blocked-country gate only fires when `country_code`
   is non-empty AND in `BLOCKED_COUNTRIES`, so a lookup failure
   doesn't accidentally block legitimate users.

5. **Resource limits parametrised, not hard-coded.** Each `mem_limit`
   / `cpus` reads from `${SANDBOX_MEM_*:-default}`. Sandbox tenants
   inherit the defaults (because `provision.py` doesn't set any of
   those vars). Launch-tier tenants can lift each cap by writing them
   into their own `.env` before `docker compose up`.

6. **Queue-delay copy is intentionally vague.** Brief said "a few
   extra minutes" is fine, no precise estimate. We measure
   `len(_in_flight_signups) >= MAX_CONCURRENT_PROVISIONS` at signup
   time, append a single-sentence note to the acknowledgement email's
   `next_step` paragraph, and leave it at that.

7. **422 with structured `detail`.** All three abuse-protection
   rejections (disposable, turnstile, blocked region) return a 422
   with `{"detail": {"ok": false, "error": "...", "message": "..."}}`
   so the client can render the server's message inline without
   hard-coding strings.

## Verification (run locally)

```
cd proposals/hatchik/signup-service
HATCHIK_SIGNUP_DB=/tmp/h.db HATCHIK_GEO_IP_DISABLED=1 \
  HATCHIK_MAX_CONCURRENT_PROVISIONS=2 \
  python -m uvicorn main:app

# Disposable email → 422
curl -X POST localhost:8090/api/signup -H 'Content-Type: application/json' \
  -d '{"email":"t@mailinator.com","product_name":"foo","description":"bar"}'

# Burst signups → first 2 dispatch, rest queue (status='queued')
for i in 1 2 3 4 5; do
  curl -X POST localhost:8090/api/signup -H 'Content-Type: application/json' \
    -d "{\"email\":\"u$i@example.com\",\"product_name\":\"app$i\",\"description\":\"x\"}"
done
sqlite3 /tmp/h.db 'SELECT id, status, provision_started_at FROM signups ORDER BY id'

# Admin geo dashboard
HATCHIK_ADMIN_TOKEN=dev curl -H 'X-Admin-Token: dev' \
  http://localhost:8090/api/admin/signups/geo
```

Locally verified during implementation:
- disposable email → 422 with structured detail
- real signup → 201, new columns populated, status transitions
  `new → provisioning → live`
- burst signup → first N dispatch, rest land in `status='queued'`
- admin geo endpoint → 403 without token, 200 with token, returns
  `window_days`, `by_country`, `recent`

## Open questions / follow-ups

1. **Turnstile sitekey placeholder.** All three HTML pages currently
   hard-code `"<TURNSTILE_SITEKEY_HERE>"`. Deploy script should rewrite
   this before publishing — recommend a `sed -i` step that takes the
   sitekey from `/etc/hatchik-signup.env` and substitutes it into the
   three HTML files at deploy time. Until then the widget renders an
   "invalid sitekey" warning and the server-side check no-ops because
   `HATCHIK_TURNSTILE_SECRET` is empty.

2. **ipapi.co rate limit.** Free tier is ~1,000 requests/day per IP.
   At low signup volume that's plenty, but if a single VPN-using
   attacker hammers `/api/signup` from one source IP we'll exhaust the
   quota and subsequent legitimate lookups will fail silently (returning
   empty geo, which is fine — geo is advisory only). If volume grows,
   switch `HATCHIK_GEO_IP_URL` to a self-hosted MaxMind GeoLite2 lookup
   or a paid tier.

3. **Queue starvation across uvicorn restarts.** Existing rows with
   `status='provisioning'` will not be retried — if uvicorn crashes
   mid-provision, the row sticks at `provisioning` forever and the
   slot is "lost" until the worker is cycled. A future improvement:
   on startup, re-queue any `status='provisioning'` rows older than N
   minutes by flipping them back to `'queued'`. Not done here because
   it crosses into provisioning-correctness territory that the brief
   said to leave alone.

4. **Disposable email list staleness.** The hard-coded set is current
   as of implementation but throwaway providers churn fast. Consider
   adding a low-priority Sprint to swap in a curated subset of the
   GitHub `disposable-email-domains/disposable-email-domains` list,
   regenerated quarterly via a small build-time script.

5. **Admin token gating on the geo endpoint.** Reuses the existing
   `HATCHIK_ADMIN_TOKEN` — same shared secret as the
   account-decommission endpoints. If this token leaks, all admin
   surfaces are exposed. Worth considering a separate read-only
   `HATCHIK_FRAUD_DASHBOARD_TOKEN` later.

6. **Login-form Turnstile resets on every 422.** Because Turnstile
   tokens are single-use, the client resets the widget on any 422.
   This is correct, but the UX could be friendlier — e.g. only reset
   when the error code is specifically `turnstile_failed`. Left as a
   polish item for the wizard sprint.
