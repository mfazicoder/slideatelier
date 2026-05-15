# Runbook: 2-hour idle-suspend (Hatchik sandboxes)

## What this does

The Hatchik sandbox host runs ~45 per-tenant Docker Compose stacks at
`/opt/hatchik-tenants/<slug>/`. Always-on costs us ~£1.20/mo per tenant in
RAM share. Suspending containers after 2 idle hours and waking them on
first request cuts that to ~£0.30/mo, which is the price the Sandbox tier
is sold at.

Two services collaborate:

| Service | What |
|---|---|
| `hatchik-idle-suspend.service` | Polls every 2 min. Reads the host-Caddy access log to find each tenant's last HTTP request. If >2h have passed, runs `docker compose stop` for that tenant. |
| `hatchik-wake-on-request.service` | HTTP shim on `127.0.0.1:18999`. Caddy proxies to it as an error fallback when a tenant port is unreachable. Runs `docker compose start`, polls until ready, returns a "warming up" splash with auto-refresh. Customer hits the real sandbox after one refresh (~10-15s). |

Both write structured event logs to `/var/log/hatchik/`:
- `/var/log/hatchik/idle-suspend.log`
- `/var/log/hatchik/wake-on-request.log`

Both also log human-readable lines to journald via systemd.

## How a typical 2-hour cycle looks

```
t=0       customer hits prepsheet.hatchik.com → tenant warm, served normally
t=120m    idle_suspend.py tick: idle=7200s, container running → docker compose stop
t=121m    customer revisits → Caddy gets ECONNREFUSED on :18000 → 502
          → Caddy handle_errors → wake-on-request shim
          → docker compose start; poll :18000 every 1s
          → ~12s later port is open → 200 + meta-refresh splash
t=121m05  browser auto-refreshes → Caddy primary path → tenant → real page
```

## Install on the sandbox host

```bash
# 1. Drop code into place (the orchestrator dir provision.py already lives in)
install -m 0755 idle_suspend.py     /opt/hatchik-orchestrator/
install -m 0755 wake_on_request.py  /opt/hatchik-orchestrator/

# 2. Drop systemd units
install -m 0644 systemd/hatchik-idle-suspend.service     /etc/systemd/system/
install -m 0644 systemd/hatchik-wake-on-request.service  /etc/systemd/system/

# 3. Set up the log dir
install -d -o root -g root -m 0755 /var/log/hatchik

# 4. Make sure the host Caddyfile writes the JSON access log the suspend
#    daemon reads. See host-caddy/access-log.snippet — copy the `log`
#    block into the globals section of /opt/hatchik-host-caddy/Caddyfile
#    and bind-mount /opt/hatchik-host-caddy/logs into the host-caddy
#    container at /var/log/caddy. Then:
docker exec hatchik-host-caddy-caddy-1 caddy reload --config /etc/caddy/Caddyfile

# 5. Re-render every existing tenant's Caddy route with the new template
#    (handle_errors → wake shim). Easiest: bump provision.py's template,
#    then for slug in $(jq -r '.tenants|keys[]' /opt/hatchik-tenants/registry.json); do
#      provision.py --rerender $slug    # idempotent — no compose changes
#    done
#    Or hand-patch tenants.d/*.caddy and `caddy reload`.

# 6. Enable + start
systemctl daemon-reload
systemctl enable --now hatchik-wake-on-request.service
systemctl enable --now hatchik-idle-suspend.service

# 7. Verify
systemctl status hatchik-idle-suspend.service hatchik-wake-on-request.service
journalctl -u hatchik-idle-suspend.service -n 50 --no-pager
journalctl -u hatchik-wake-on-request.service -n 50 --no-pager
```

## Opt a tenant out

Some scenarios where you want a sandbox to NEVER auto-suspend:
- A customer demo is happening and we want zero wake latency.
- We're debugging an intermittent bug whose repro requires the substrate
  to stay warm.
- A scheduled job inside the tenant container fires hourly (rare but
  possible).

Drop a `lifecycle.json` into the tenant's directory:

```bash
cat > /opt/hatchik-tenants/<slug>/lifecycle.json <<'JSON'
{
  "idle_suspend": false,
  "note": "demo for ACME Corp 2026-05-20 — re-enable after that date"
}
JSON
```

The next idle-suspend tick reads it, skips the tenant, and writes a
`skip-opt-out` event noting the reason.

To re-enable: delete the file or set `idle_suspend: true`.

## Monitoring

### Daily check

```bash
# How many tenants are currently suspended?
docker ps --format '{{.Names}}' | awk -F- '{print $1}' | sort -u > /tmp/running.txt
jq -r '.tenants | to_entries[] | select(.value.status=="live") | .key' \
  /opt/hatchik-tenants/registry.json | sort -u > /tmp/live.txt
comm -23 /tmp/live.txt /tmp/running.txt   # live in registry but not running
```

### Suspect a wake is broken

```bash
# What wake events have fired today?
grep "$(date -u +%Y-%m-%d)" /var/log/hatchik/wake-on-request.log | jq -c .

# Were any failures?
jq -c 'select(.action=="wake-failed")' /var/log/hatchik/wake-on-request.log | tail -20
```

### Suspect the suspend daemon is sleeping

```bash
# Last tick time
journalctl -u hatchik-idle-suspend.service | grep -E '^.*tick now=' | tail -3

# Most recent event-log line
tail -n 1 /var/log/hatchik/idle-suspend.log | jq .

# If the lock file is stale (process died, never released):
ls -l /var/lock/hatchik-idle-suspend.lock
# systemd's flock release on process exit handles the common case, but if
# you've been editing files in /var/lock manually:
sudo rm /var/lock/hatchik-idle-suspend.lock
systemctl restart hatchik-idle-suspend.service
```

## What to do if wake fails

A wake failure means `docker compose start` returned non-zero OR the
tenant port never came up within `HATCHIK_WAKE_HEALTHCHECK_TIMEOUT` (30s
default). The customer sees the 503 "Sandbox temporarily unavailable"
page with a Retry-After: 30.

Triage:

```bash
slug=prepsheet

# 1. Look at the wake event line for context
jq -c "select(.slug==\"$slug\" and .action==\"wake-failed\")" \
  /var/log/hatchik/wake-on-request.log | tail -5

# 2. Try the start by hand
cd /opt/hatchik-tenants/$slug
docker compose ps
docker compose start
docker compose logs --tail=200

# 3. If a container is OOMing or stuck restarting, the substrate is
#    crash-looping. Bring it down and back up cleanly:
docker compose down
docker compose up -d
docker compose logs -f
```

If the cause is an underlying substrate issue (e.g., postgres data
corruption), see `restore.py` and the archive at `/var/hatchik-archive/`.

## Testing

### Smoke test (no live tenants needed) — dry-run against a synthetic state dir

```bash
export HATCHIK_IDLE_SUSPEND_STATE_DIR=/tmp/hatchik-idle-smoke
export HATCHIK_IDLE_SUSPEND_CADDY_LOG=/tmp/hatchik-idle-smoke/access.log
export HATCHIK_IDLE_SUSPEND_LOG_DIR=/tmp/hatchik-idle-smoke/logs
export HATCHIK_IDLE_SUSPEND_LOCK_FILE=/tmp/hatchik-idle-smoke/idle.lock

mkdir -p $HATCHIK_IDLE_SUSPEND_STATE_DIR/{busy,quiet,optout,promoted}
cat > $HATCHIK_IDLE_SUSPEND_STATE_DIR/registry.json <<'JSON'
{
  "version": 1,
  "tenants": {
    "busy":     {"status": "live", "port": 18000, "email": "a@a", "created_at": "2026-04-01T00:00:00Z"},
    "quiet":    {"status": "live", "port": 18001, "email": "b@b", "created_at": "2026-04-01T00:00:00Z"},
    "optout":   {"status": "live", "port": 18002, "email": "c@c", "created_at": "2026-04-01T00:00:00Z"},
    "promoted": {"status": "live", "port": 18003, "email": "d@d", "created_at": "2026-04-01T00:00:00Z", "promoted_to": "launch", "promoted_at": "2026-04-15T00:00:00Z"}
  }
}
JSON

# busy: had a request 5 min ago — should be skip-active
# quiet: last request 4h ago — should be would-suspend
# optout: opted out — should be skip-opt-out
# promoted: last request 4h ago, but promoted to launch — should ALSO be would-suspend
#           (per the brief; differs from lifecycle.py which exempts promoted tenants
#           from ARCHIVE, but suspend applies regardless of plan)

now_epoch=$(python3 -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).timestamp())')
busy_ts=$(python3 -c "print($now_epoch - 300)")     # 5 min ago
quiet_ts=$(python3 -c "print($now_epoch - 14400)")  # 4h ago
prom_ts=$(python3 -c "print($now_epoch - 14400)")   # 4h ago
cat > $HATCHIK_IDLE_SUSPEND_CADDY_LOG <<JSON
{"ts": $busy_ts,  "request": {"host": "busy.hatchik.com"}}
{"ts": $quiet_ts, "request": {"host": "quiet.hatchik.com"}}
{"ts": $prom_ts,  "request": {"host": "promoted.hatchik.com"}}
JSON

cat > $HATCHIK_IDLE_SUSPEND_STATE_DIR/optout/lifecycle.json <<'JSON'
{ "idle_suspend": false, "note": "demo prep" }
JSON

python3 idle_suspend.py --once --dry-run --json
jq -c . $HATCHIK_IDLE_SUSPEND_LOG_DIR/idle-suspend.log
```

Expected actions (in the JSON summary): `skip-active`, `would-suspend`,
`skip-opt-out`, `would-suspend` (promoted).

### End-to-end on staging

1. Pick a low-traffic tenant.
2. `export HATCHIK_IDLE_SUSPEND_SECONDS=120` in `/opt/hatchik-orchestrator/.env`
3. `systemctl restart hatchik-idle-suspend.service`
4. Wait 4 min. `docker ps` should no longer show the tenant's containers.
5. `curl -I https://<slug>.hatchik.com` → expect 200 with the splash HTML.
6. ~12s later, `curl -I https://<slug>.hatchik.com` → expect 200 with the substrate's page.
7. Reset `HATCHIK_IDLE_SUSPEND_SECONDS` to 7200 in `.env` and restart.

## Cost note

This mechanism is what underwrites the £0.30/mo Sandbox price point. If
wake latency degrades (>30s), customer perception of the sandbox tier
suffers; raise `HATCHIK_WAKE_HEALTHCHECK_TIMEOUT` cautiously and
consider whether the host is overloaded (a sandbox host that takes 30s
to bring 1 tenant up is also slow for warm tenants).

Promoted tenants (Launch/Growth) suspend too — they're paying for the
slot, not for always-on. The Launch dashboard's "open my sandbox"
button hits Caddy, which wakes the sandbox, which serves the page after
the splash refresh. The wake-on-request splash explains why so the
customer doesn't think the app is broken.
