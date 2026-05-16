# Hatchik — full-lifecycle smoke test

Pre-launch checks for the **entire customer lifecycle**:

1. Sandbox signup → provision → live (see [SANDBOX_SMOKE_TEST.md](./SANDBOX_SMOKE_TEST.md))
2. Sandbox → Launch upgrade (Paddle subscription.created → promote.py)
3. Launch operations (services tab, mobile builds, billing portal)
4. Launch → Growth (Paddle plan-change → tier_transitions)
5. Past-due dunning (Paddle subscription.updated status=past_due)
6. Cancellation + 30-day grace + decommission
7. Sandbox idle archive + restore

Run on the **sandbox host** (`178.105.139.144`) and **launch
orchestrator host** (same box for now). ~45 minutes if nothing fails.

This complements `SANDBOX_SMOKE_TEST.md` — start with that, then layer
this on top.

---

## 0. Preflight (5 min)

```bash
ssh root@178.105.139.144

# All services and timers
systemctl is-active hatchik-signup hatchik-host-caddy \
                    hatchik-lifecycle.timer \
                    hatchik-launch-lifecycle.timer

# Launch orchestrator wiring
ls -la /opt/hatchik-launch-orchestrator/
test -r /opt/hatchik-launch-orchestrator/.env && \
    echo "✓ .env present" || echo "✗ .env missing — fill it in from .env.template"

# Required env vars resolvable
grep -E '^(HETZNER_API_TOKEN|CLOUDFLARE_API_TOKEN|RESEND_API_KEY)=' \
    /opt/hatchik-launch-orchestrator/.env | grep -v '=$' || \
    echo "✗ HETZNER_API_TOKEN / CLOUDFLARE_API_TOKEN / RESEND_API_KEY missing"

# Hetzner credentials work
cd /opt/hatchik-launch-orchestrator
source .env && export HETZNER_API_TOKEN CLOUDFLARE_API_TOKEN
python3 -c "from hetzner_api import list_servers; print('hetzner:', len(list_servers()), 'servers')"

# Cloudflare zones reachable
python3 -c "from dns_api import list_zones; print('cloudflare:', len(list_zones()), 'zones')"

# Paddle webhook secret is configured (else webhooks 500)
grep PADDLE_WEBHOOK_SECRET /opt/hatchik-signup/.env | grep -v '=$' || \
    echo "✗ PADDLE_WEBHOOK_SECRET missing"

# Paddle price IDs configured (Launch + Growth)
grep -E '^PADDLE_(LAUNCH|GROWTH)_PRICE_ID=' /opt/hatchik-signup/.env | grep -v '=$'
```

Expected: every service active, env files complete, Hetzner + Cloudflare
+ Paddle credentials valid.

---

## 1. Sandbox baseline (10 min)

Follow `SANDBOX_SMOKE_TEST.md` steps 1–6 with a fresh email like
`you+lifecycle@example.com`. End with a live `<slug>.hatchik.com`
sandbox you can sign into.

Note the **signup_id** from the DB:

```bash
sqlite3 /var/lib/hatchik/signups.db \
  "SELECT id, email, slug, status, tier FROM signups WHERE email LIKE '%+lifecycle%' ORDER BY id DESC LIMIT 1;"
```

---

## 2. Sandbox → Launch upgrade (15 min)

### 2a. Trigger checkout

Sign in to `hatchik.com/account` → **Upgrade** tab → click
**Upgrade for £89 (then £14/month)**. Complete the Paddle hosted
checkout with a Paddle test card (sandbox mode) or real card (live).

### 2b. Watch the webhook arrive

```bash
journalctl -u hatchik-signup -f
```

Expected log line:
```
Paddle subscription.created: subscription=sub_... customer=ctm_... status=active
Triggered promote.py for signup #N (event=evt_...)
```

### 2c. Check the tier_transitions row

```bash
sqlite3 /var/lib/hatchik/signups.db <<EOF
SELECT signup_id, from_tier, to_tier, paddle_event_id, notes, occurred_at
FROM tier_transitions
WHERE signup_id = <N>
ORDER BY id DESC;
EOF
```

Expected: a `sandbox → launch` row with paddle_event_id populated and
notes "paddle subscription.created".

### 2d. promote.py SAFE_MODE plan email

You should receive an email at `hello@hatchik.com` with subject
`[Launch #N] SAFE_MODE: <product_name>` containing the full provisioning
plan (Hetzner location, server class, sandbox slug, steps).

If you trust the plan, execute it:

```bash
cd /opt/hatchik-launch-orchestrator
sudo -u root python3 promote.py --signup-id <N> --execute --json
```

This will:
- Create a Hetzner CAX31 in the customer's region
- Wait for it to boot
- Update `registry.json` with the new tenant
- Attempt the Cloudflare A-record set (or fall back to manual-DNS email)
- Email founder with the SSH bootstrap step (substrate clone + DB
  restore + `docker compose up -d`)

### 2e. Bootstrap the substrate on the new VPS

Follow the founder email's bootstrap steps. Once the substrate is
serving on the new VPS:

```bash
sudo -u root python3 promote.py --signup-id <N> --mark-live --json
```

This:
- Flips `registry.tenants[launch-N].status = 'live'`
- Updates `signups.tier = 'launch'`, `status = 'live-launch'`
- Emails the customer "Your Launch tier is live"

### 2f. Verify the customer sees Launch state

In a browser at `hatchik.com/account`:
- Sandbox card no longer appears (or shows decommissioned)
- "What's set up" tab shows Launch inventory (Postgres unlimited,
  dedicated VPS, 3 mailboxes, etc.)
- "Billing" tab opens the Paddle customer portal
- "Upgrade" tab now says "Already on Launch" (or hides the button)

---

## 3. Launch operations (5 min)

Test every Launch-tier surface against the new tenant:

| Surface | What | Expected |
|---|---|---|
| Customer domain | `https://<customer-domain>/` | 200, substrate landing page |
| Services tab | `/account` → What's set up | Launch inventory rendered |
| Mobile builds | `/account` → Mobile builds → Trigger | Workflow dispatches, build queued |
| Settings | Update GitHub username | Repo re-invite fires |
| Redeploy | `git push main` on customer repo | Tenant rebuilds within 2 min |

---

## 4. Launch → Growth plan change (5 min)

In Paddle dashboard, modify the customer's subscription to swap the
Launch price item for the Growth price item.

Watch:

```bash
journalctl -u hatchik-signup -f
```

Expected: `Paddle subscription.updated: ... status=active`.

Check tier_transitions:

```bash
sqlite3 /var/lib/hatchik/signups.db \
  "SELECT from_tier, to_tier, notes FROM tier_transitions WHERE signup_id = <N> ORDER BY id;"
```

Expected: a new `launch → growth` row with notes "paddle plan change to
growth".

Note: the VPS resize (CAX31 → CAX41) is **manual** for now — the
launch_lifecycle.py daily run picks up the growth tier transition and
emails the founder a resize runbook. Do that manually when convenient.

---

## 5. Past-due dunning (10 min, only run if comfortable)

In Paddle dashboard, simulate a payment failure (cancel the test card
or use the failure-test card in sandbox mode).

Watch the webhook fire:
```bash
journalctl -u hatchik-signup -f
```

Expected: `Paddle subscription.updated: ... status=past_due` and
`Paddle transaction.payment_failed`.

Trigger a launch_lifecycle.py run (would normally wait for the daily
timer):

```bash
cd /opt/hatchik-launch-orchestrator
sudo -u root python3 launch_lifecycle.py --dry-run --json
```

Expected: the tenant shows up under `past_due` (with action `none` at
day 0 — the ladder kicks in at day 3). To simulate later days, edit
`registry.json` and set a fake `past_due_since` field, then re-run.

---

## 6. Cancellation + grace (5 min)

In the Paddle customer portal (Billing tab in account.html), cancel
the subscription.

Watch:
```bash
journalctl -u hatchik-signup -f
```

Expected: `Paddle subscription.canceled (churn): subscription=sub_...`.

Verify:
- `tier_transitions` has a `launch → cancelled` row
- `registry.json` for this tenant now has `canceled_at: <iso timestamp>`
- The customer received the "30-day grace" email

Then run the reconciler in dry-run mode:

```bash
sudo -u root python3 launch_lifecycle.py --dry-run --json
```

The tenant should classify as `cancel_grace` with `days=0`.

Simulate day 25 by editing the `canceled_at` to 25 days ago and re-run:
the tenant should classify as `cancel_warn`.

Simulate day 30+ — the tenant should classify as `decom`. In
**SAFE_MODE** the reconciler will send an email; with `--execute` it
calls `decommission_launch.py --execute` which snapshots + tears down
the VPS.

---

## 7. Sandbox idle archive (auto, 30 days)

This one runs automatically. To verify:

```bash
sudo -u root python3 /opt/hatchik-orchestrator/lifecycle.py --dry-run --json
```

For a sandbox slug, fake the last-seen-at to 23 days ago and re-run:
the tenant should classify under `warn_day_23`. Day 30 → `archive`,
day 37 → `purge`.

---

## 8. Pricing surface scan (1 min)

```bash
for url in https://hatchik.com/ https://hatchik.com/start.html \
           https://hatchik.com/vs.html https://hatchik.com/docs/faq.html \
           https://hatchik.com/docs/what-is-included.html; do
  echo "=== $url ==="
  curl -sSL "$url" | grep -oE '£[0-9]+(\.[0-9]+)?(/(mo|month|yr|year))?' | sort -u
done
```

Expected: only `£0`, `£14/mo`, `£39/mo`, `£89` setup, and historical
`£9` / `£24` inside reprice-narrative copy.

---

## 9. Done

If steps 1–8 cleared without surprises, the **entire lifecycle is
production-ready**.

What's still **deferred to manual operator work** (intentionally):

- Substrate bootstrap on a freshly-provisioned Launch VPS (clone repo,
  restore DB snapshot, `docker compose up -d`). promote.py emails the
  founder the SSH commands. Automate when you've done it 3+ times by
  hand and have a clean script.
- Launch → Growth VPS resize (CAX31 → CAX41). launch_lifecycle.py
  flags this; do via Hetzner Cloud console (15 minutes).
- Past-due `suspend` action: launch_lifecycle.py emails the founder
  rather than auto-shutting containers. One-click manual suspend is
  fine until you have >50 Launch customers.

Once the first 5 Launch customers go through end-to-end, you'll know
which of those three to automate next. The smoke test above will be
the regression check.
