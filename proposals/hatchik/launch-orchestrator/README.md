# Hatchik Launch Orchestrator

Provisioning, migration, and lifecycle for the **Launch tier** (£89 setup +
£14/month) and **Growth tier** (£39/month). Launch tenants run on dedicated
Hetzner VPSes in the customer's region, on the customer's own domain (BYO
or registered for them).

Counterpart to `sandbox-orchestrator/` (shared-host shared-port Sandbox tier).

## Tier map

| Tier | Host | Domain | Lifecycle |
|---|---|---|---|
| Sandbox | shared CAX21 at `*.hatchik.com` | `<slug>.hatchik.com` | 30d idle → archive |
| Launch  | dedicated CAX31 in customer region | customer's domain | Paddle-driven |
| Growth  | same VPS, more headroom | customer's domain | Paddle-driven |

## Pieces

| File | What |
|---|---|
| `promote.py` | Triggered by Paddle `subscription.created` webhook. Promotes a Sandbox tenant to Launch: provisions a dedicated VPS, runs the substrate deploy bundle, migrates the database, points DNS at the new IP, decommissions the sandbox slug. |
| `decommission_launch.py` | Tears down a Launch VPS — snapshots data, releases VPS, archives. Triggered at end-of-grace for canceled subscriptions. |
| `launch_lifecycle.py` | Daily reconciler: walks Launch tenants, syncs status with Paddle (active / past_due / canceled), dunns past_due, decommissions canceled at end of grace. Runs from `hatchik-launch-lifecycle.timer`. |
| `hetzner_api.py` | Thin wrapper over Hetzner Cloud API (create_server / delete_server / list_servers / snapshot). |
| `dns_api.py` | Thin wrapper over Cloudflare DNS API (set A record on customer domain to new VPS IP). |
| `tenant_inventory.py` | Source-of-truth: what's wired in a Launch tenant. Mirrors `service_inventory.py` for Launch — exposed via the same `/api/account/services/{slug}` endpoint. |
| `registry.json` | Source of truth for Launch tenants (slug → VPS ID, region, IP, status, customer domain, Paddle subscription_id). |
| `hatchik-launch-lifecycle.service` + `.timer` | systemd units that fire `launch_lifecycle.py` daily. |

## Safety: SAFE_MODE default

`promote.py` and `decommission_launch.py` default to **SAFE_MODE**: they
compute the plan, send the founder an actionable email with the next-step
runbook, and write a transition row — but they do not actually call
Hetzner / Cloudflare APIs to provision or tear down VPSes.

This is the right default for the early launch period. Real Launch
upgrades cost real money (Paddle has been paid, customer expects a working
VPS within minutes) but they're also **rare** in the first weeks — there's
no reason to fire off destructive API calls when a 5-minute manual nudge
will catch any edge case the script didn't anticipate.

Flip to `--execute` once you've:
1. Verified `HETZNER_API_TOKEN` works (`python3 -c 'from hetzner_api import list_servers; print(list_servers()[:1])'`)
2. Verified `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` work (`python3 -c 'from dns_api import list_zones; print(list_zones()[:1])'`)
3. Run promote.py end-to-end against a test sandbox you own
4. Confirmed the resulting VPS serves the substrate template correctly

## Paddle webhook → promote.py wiring

```
Paddle subscription.created event
        │
        ▼
signup-service/main.py
  → resolve customer_email → signup_id
  → INSERT tier_transitions (signup_id, 'sandbox', 'launch', now, event_id, 'paddle webhook')
  → asyncio.create_task(launch-orchestrator/promote.py --signup-id $ID)
        │
        ▼
promote.py (SAFE_MODE by default)
  ├─ Validate: signup is currently tier='sandbox', status='live'
  ├─ Compute: region, VPS class, domain choice (BYO vs register)
  ├─ Provision (if --execute): Hetzner CAX31 in region
  ├─ Wait for boot, install Docker, rsync substrate
  ├─ Restore DB from sandbox snapshot
  ├─ DNS: A record customer-domain → new VPS IP (Cloudflare)
  ├─ Wait for TLS provisioning
  ├─ Decommission sandbox slug (free port on shared host)
  ├─ Mark signup tier='launch', status='live-launch'
  └─ Email customer: "Your Launch tier is ready"
```

## Paddle webhook → decommission_launch.py wiring

```
Paddle subscription.canceled event
        │
        ▼
signup-service/main.py
  → resolve customer_email → signup_id
  → INSERT tier_transitions (signup_id, 'launch', 'cancelled', now, event_id, 'paddle cancel')
  → mark canceled_at on launch registry
        │
        ▼ (30 days later, via launch_lifecycle.py)
launch_lifecycle.py daily run
  ├─ Find tenants with canceled_at < now - 30d
  ├─ decommission_launch.py --slug <s> (SAFE_MODE by default)
  └─ Email founder: "Tenant ready for tear-down, run --execute when ready"
```

## Past-due / dunning timeline

Paddle handles retry on its end (smart dunning, configurable in dashboard).
Our reconciler watches `subscription.status` and acts at:

| Day | Action |
|-----|--------|
| 0 | First retry by Paddle. We log. |
| 3 | Email customer: "Payment failed, here's the Paddle portal link." |
| 7 | Email customer: "Your Launch tier suspends in 2 days unless we hear from you." |
| 9 | **Suspend** — containers down, data preserved, custom 503 page on customer domain explaining how to restore. |
| 30 | Hard decommission — snapshot, archive, release VPS. |

Most of this is on the customer's side (Paddle dashboard). Our role is
gracefully suspending the tenant rather than serving 500s.

## Operator quick-start

```bash
# One-time setup on the orchestrator host (probably the same Infomaniak VPS
# that hosts signup-service)
cd /opt/hatchik-launch-orchestrator
cp .env.template .env  # fill in HETZNER_API_TOKEN, CLOUDFLARE_API_TOKEN, RESEND_API_KEY
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Install systemd timer
install -m 0644 hatchik-launch-lifecycle.service /etc/systemd/system/
install -m 0644 hatchik-launch-lifecycle.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hatchik-launch-lifecycle.timer

# Smoke-test a dry-run promote
python3 promote.py --signup-id <N>            # SAFE_MODE — emails plan
python3 promote.py --signup-id <N> --execute   # real provisioning

# Daily reconcile (auto via timer; this is the manual handle)
sudo -u root python3 launch_lifecycle.py --dry-run --json
```

## Why a separate orchestrator vs extending sandbox-orchestrator

- **Different infrastructure**: Sandbox is shared-host with port-routed Caddy.
  Launch is dedicated-VPS with customer-domain TLS. The provisioning code
  paths diverge fundamentally.
- **Different lifecycle**: Sandbox is time-based (30d idle → archive). Launch
  is billing-status-driven (active / past_due / canceled with grace).
- **Different blast radius**: A bug in sandbox-orchestrator decommissions a
  free shared-host tenant. A bug in launch-orchestrator destroys a paying
  customer's VPS. Separate code paths → blast radius is contained.
- **Different concurrency**: Sandbox provisions complete in ~3 min on a
  shared host. Launch VPS provisioning + DNS propagation + TLS provisioning
  is closer to 10-15 min. Different queue characteristics.
