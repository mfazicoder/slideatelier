# Hatchik status service — install

Self-hosted uptime monitor that powers `status.hatchik.com`.

Components covered:

- Marketing site (`hatchik.com/`)
- Signup API (`hatchik.com/api/healthz`)
- Sandbox provisioning (one live tenant URL from the orchestrator registry)
- Host TLS (wildcard cert + Cloudflare DNS-01)
- Sandbox host metrics (disk used %, free RAM, 1-minute load)
- Tenant fleet summary (live / provisioning / failed / archived counts)

The service stores every probe result to SQLite at `/var/lib/hatchik/status.db`,
serves a cached JSON snapshot from `/api/status`, and the static page at
`status.hatchik.com` consumes that JSON over fetch every 30s.

## Files in this directory

- `main.py` — FastAPI app + background probe loop
- `requirements.txt` — fastapi, uvicorn, httpx, pydantic (same deps as signup-service)
- `hatchik-status.service` — systemd unit, runs uvicorn on `127.0.0.1:8091`
- `INSTALL.md` — this file

Sibling files referenced below:

- `../status.html` — the static dark-themed status page
- `../sandbox-orchestrator/host-caddy/Caddyfile` — patched to route `status.hatchik.com`

## Deploy

From your local machine:

```bash
# 1. Sync the service code to the sandbox host
rsync -avz proposals/hatchik/status-service/ \
    root@178.105.139.144:/opt/hatchik-status/

# 2. Sync the static status page next to the other marketing files
rsync -avz proposals/hatchik/status.html \
    root@178.105.139.144:/var/www/hatchik/status.html

# 3. Sync the patched Caddyfile to the host Caddy
rsync -avz proposals/hatchik/sandbox-orchestrator/host-caddy/Caddyfile \
    root@178.105.139.144:/opt/hatchik-host-caddy/Caddyfile
```

SSH to the host:

```bash
ssh root@178.105.139.144

# 4. Create the venv and install deps
cd /opt/hatchik-status
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 5. Ensure the SQLite DB directory exists and is writable by www-data
mkdir -p /var/lib/hatchik
chown www-data:www-data /var/lib/hatchik
# The service bootstraps an empty status.db on first start — no migration step.

# 6. Install + enable the systemd unit
cp hatchik-status.service /etc/systemd/system/
# Optional: open the unit and set HATCHIK_STATUS_ADMIN_TOKEN to a random
# secret if you want POST /api/status/incident enabled.
systemctl daemon-reload
systemctl enable --now hatchik-status
systemctl status hatchik-status

# 7. Reload the host Caddy to pick up the new status.hatchik.com block
docker exec hatchik-host-caddy-caddy-1 \
    caddy reload --config /etc/caddy/Caddyfile
```

## DNS

The host Caddy already holds a wildcard cert for `*.hatchik.com` via Cloudflare
DNS-01, so `status.hatchik.com` works as soon as Cloudflare resolves the
subdomain to the sandbox host's IP (`178.105.139.144`).

Verify in the Cloudflare dashboard that one of these is true:

1. A wildcard `A` record exists at `*.hatchik.com → 178.105.139.144` (covers
   any subdomain automatically), **or**
2. An explicit `A` record for `status.hatchik.com → 178.105.139.144`.

If neither is present, add the explicit record (proxied/orange-cloud is fine).

## Verify

From your laptop:

```bash
# Static page renders
curl -sI https://status.hatchik.com/ | head -1
# → HTTP/2 200

# JSON snapshot
curl -s https://status.hatchik.com/api/status | jq .overall
# → "operational"

# Service log
ssh root@178.105.139.144 journalctl -u hatchik-status -f
```

The first JSON snapshot is generated at startup (inline probe inside the
lifespan handler) so `/api/status` returns useful data within a couple of
seconds of the service coming up.

## Manual incident (optional)

If you set `HATCHIK_STATUS_ADMIN_TOKEN=$SECRET` in the systemd unit, you can
publish a manual banner that overlays the auto-detected status:

```bash
curl -X POST https://status.hatchik.com/api/status/incident \
    -H "X-Admin-Token: $SECRET" \
    -H "Content-Type: application/json" \
    -d '{"title": "Investigating slow signup API", "body": "Resend webhook backlog — fix ETA 20m", "severity": "minor"}'

# Resolve when fixed:
curl -X POST https://status.hatchik.com/api/status/incident \
    -H "X-Admin-Token: $SECRET" \
    -H "Content-Type: application/json" \
    -d '{"title": "ignored", "resolve": true}'
```

## Roll back

```bash
ssh root@178.105.139.144 systemctl disable --now hatchik-status
# Remove the status.hatchik.com block from /opt/hatchik-host-caddy/Caddyfile
# (or revert via git), then:
docker exec hatchik-host-caddy-caddy-1 \
    caddy reload --config /etc/caddy/Caddyfile
```

No state external to the host is touched — the SQLite DB at
`/var/lib/hatchik/status.db` can be deleted without affecting any other
Hatchik service.
