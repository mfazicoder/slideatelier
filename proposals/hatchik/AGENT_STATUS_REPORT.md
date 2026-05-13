# Agent status — build of `status.hatchik.com`

Worktree-only build. Nothing deployed; everything sits ready for rsync.

## Files added

| Path | Purpose |
| --- | --- |
| `proposals/hatchik/status.html` | Dark, Linear-style static status page. Polls `/api/status` every 30s, renders hero banner, per-component rows with 30-day uptime sparklines, host metric mini-bars, and a tenant fleet summary. Self-contained (only external dep is Tailwind-less hand-written CSS + Inter/JetBrains Mono from Google Fonts). |
| `proposals/hatchik/status-service/main.py` | FastAPI service. Background probe loop every 60s hits marketing, signup `/api/healthz`, one live tenant URL (from orchestrator `registry.json`), the host TLS path, and reads local host metrics. Persists every check to `/var/lib/hatchik/status.db`. Serves `/api/status`, `/api/status/history.json`, `/healthz`, and an admin-gated `POST /api/status/incident`. |
| `proposals/hatchik/status-service/requirements.txt` | Same deps as signup-service (fastapi, uvicorn, httpx, pydantic) — no new transitive deps. |
| `proposals/hatchik/status-service/hatchik-status.service` | systemd unit. Runs uvicorn on `127.0.0.1:8091` as `www-data`. Mirrors the signup-service unit's shape. |
| `proposals/hatchik/status-service/INSTALL.md` | rsync paths, systemd enable, Caddy reload, DNS note (wildcard `*.hatchik.com` already covers `status`). |

## Files modified

| Path | Change |
| --- | --- |
| `proposals/hatchik/sandbox-orchestrator/host-caddy/Caddyfile` | Appended a `status.hatchik.com { ... }` block after the apex `hatchik.com` block. Routes `/api/*` → `localhost:8091`, everything else → `/var/www/hatchik/status.html`. Uses the same wildcard-cloudflare TLS pattern as the apex; adds `Cache-Control: no-store` so the status page is never cached. |

## Files verified, no change

- `proposals/hatchik/index.html` line 1623 already links to `https://status.hatchik.com` in the footer — no edit needed.

## Design notes

- **Cache-first reads**: `/api/status` serves from an in-memory `CACHE` dict
  populated by the probe loop. Reads never block on probes. First probe runs
  inline inside the lifespan handler so the endpoint returns useful data
  within ~2s of cold start.
- **Auto-bootstrap on empty DB**: `init_db()` creates the parent directory
  and runs `CREATE TABLE IF NOT EXISTS …` for both tables. Deleting
  `/var/lib/hatchik/status.db` is safe and recoverable.
- **Tenant probe skip when fleet empty**: `probe_tenant` returns
  `operational` with `detail="no live tenants to probe"` when the registry
  has zero live tenants. The component row will still render.
- **TLS line is "implied"**: a successful HTTPS GET to `hatchik.com` proves
  the wildcard cert chain. We surface it as a separate component for clarity
  but it does not require its own network call beyond the marketing probe.
  (Currently it re-does the same GET — could be optimized to reuse, but
  60s × 1 extra request/min is trivial.)
- **30-day uptime ratio**: derived on the fly from raw checks with
  `GROUP BY substr(checked_at, 1, 10)`. Days with no samples render as
  hollow bars at 60% height in the sparkline. Old checks pruned daily;
  retention is 31 days.
- **Worst-of rollup**: any "down" component flips the hero red; any
  "degraded" flips it amber; otherwise green.
- **Admin incident endpoint**: gated by `HATCHIK_STATUS_ADMIN_TOKEN`. If the
  env var is empty the route returns 503 — opt-in to enable.

## Test results

Locally smoke-tested with stubbed `fastapi`/`httpx`/`pydantic`:

- `init_db()` creates schema cleanly on missing file.
- `load_registry()` returns `{"version":1,"tenants":{}}` when registry is
  absent (no crash).
- `tenant_fleet_summary()` returns zero counts on empty fleet.
- `probe_host()` returns `operational` on macOS (disk reads fine, mem/load
  silently None — won't fault the rollup).
- `record_check` + `latest_check` round-trip a probe result.
- `overall_status` rollup: `degraded → partial_outage`,
  `down → major_outage`. Verified.

Python `ast.parse` succeeds on both `status-service/main.py` and the new
Caddyfile is syntactically a normal site block (mirrors the apex block
already in the file).

## Open questions

1. **Cloudflare DNS for `status.hatchik.com`** — the install guide says
   "verify the wildcard A record exists or add an explicit one". I cannot
   check the live DNS from here; needs a human verification step before
   the Caddy block will issue a cert.
2. **`/api/healthz` proxy on apex** — the existing apex Caddy block already
   proxies `/api/*` → `localhost:8090`, so `https://hatchik.com/api/healthz`
   should reach the signup-service's `@app.get("/healthz")`. Worth a quick
   `curl` confirmation post-deploy. If signup-service exposes it at
   `/healthz` only (not `/api/healthz`), I'd recommend tightening the
   probe to whichever path actually works — easy one-line edit in
   `SIGNUP_HEALTH_URL`.
3. **Admin token** — left empty in the unit file by default. Founder should
   set a random secret in `/etc/systemd/system/hatchik-status.service`
   before relying on manual incident publishing.
4. **Cron-like reseeding** — the service prunes old rows once every 24h
   while running. If the service is stopped for >24h, no pruning happens
   that day — harmless, just means the first probe after restart eats a
   slightly larger DELETE. Acceptable.
5. **No history-import path** — sparklines start empty on first deploy.
   The page renders "no incidents reported yet" until 24h of data
   accumulates. This is the desired behaviour per the brief.

## Roll-forward checklist

1. rsync `proposals/hatchik/status-service/` → `/opt/hatchik-status/`
2. rsync `proposals/hatchik/status.html` → `/var/www/hatchik/status.html`
3. rsync the patched Caddyfile → `/opt/hatchik-host-caddy/Caddyfile`
4. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
5. `cp hatchik-status.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now hatchik-status`
6. `docker exec hatchik-host-caddy-caddy-1 caddy reload --config /etc/caddy/Caddyfile`
7. Verify `curl -sI https://status.hatchik.com/` → 200 and
   `curl -s https://status.hatchik.com/api/status | jq .overall` → string.

All instructions in `proposals/hatchik/status-service/INSTALL.md`.
