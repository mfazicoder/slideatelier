# Hatchik alpha test — full lifecycle end-to-end

Walk a real signup through **Sandbox → Launch → Growth** on staging
infra, **without paying Paddle and without waiting for 15 end-user
signups**. Two admin endpoints + one tunable env var do the bypass; the
rest of the pipeline is unchanged from production.

## Prerequisites

You need a host with the substrate-template, sandbox-orchestrator,
launch-orchestrator, and signup-service deployed. The endpoints below
assume `https://hatchik.com` — substitute your staging hostname if
different. Set `HATCHIK_ADMIN_TOKEN` on the signup-service host (any
strong random string); same value goes in the `X-Admin-Token` header
of every admin curl below.

### Credentials needed (real provisioning)

For `execute=1` calls (which actually provision real Hetzner boxes,
register real domains, etc.) the orchestrator host needs:

| Env var | What |
|---|---|
| `HETZNER_API_TOKEN` | Hetzner Cloud project token |
| `CLOUDFLARE_API_TOKEN` | Cloudflare zone-edit token for `hatchik.com` |
| `HATCHIK_GITHUB_ORG` + `HATCHIK_GITHUB_TOKEN` | Where tenant repos get created |
| `RESEND_API_KEY` | Transactional email (falls back to stdout if missing) |
| `HATCHIK_PORKBUN_API_KEY` + `HATCHIK_PORKBUN_SECRET` | Domain registration (Launch step) |
| `HATCHIK_INFOMANIAK_API_TOKEN` + `HATCHIK_INFOMANIAK_MAIL_SERVICE_ID` | Mailbox provisioning (Launch step) |
| `PADDLE_WEBHOOK_SECRET` | Not needed for the bypass paths but unblocks the real Paddle webhook |

**Without any of these the admin endpoints still work** — they default
to SAFE_MODE (`execute=0`), which just emails the founder a plan and
records audit rows. The first dry run should always be SAFE_MODE.

---

## Step 1 — Create the Sandbox

Customer-facing signup, no bypass needed. Either path works:

### Path A — web wizard

```bash
curl https://hatchik.com/api/signup \
  -H 'Content-Type: application/json' \
  -d '{
    "email": "alpha-test@hatchik.com",
    "first_name": "Alpha",
    "product_name": "AlphaTest",
    "description": "End-to-end alpha walk-through",
    "tier": "sandbox",
    "accepted_terms": true
  }'
```

### Path B — MCP wizard (conversational signup)

In your AI tool with the empty-env Hatchik MCP installed:

> "Use Hatchik to set up a meal-prep app called AlphaTest."

The AI walks you through `start_signup` → `set_choices` → `checkout`.
For Sandbox tier `checkout` skips Paddle entirely and returns an
`install_token` directly; the AI then calls `status` until ready and
`complete` to mint the `hk_live_*` key.

### Verify

```bash
# Resolves to the signups row + provisioning state.
curl -s https://hatchik.com/api/account/me \
  -H "Cookie: hatchik_session=$YOUR_SESSION"
```

The Sandbox should be live at `https://alphatest.hatchik.com` within
a few minutes (60-90s on warm infrastructure; up to ~5 min if Caddy is
fetching a fresh cert). Sign up an end-user via the live URL to prove
auth + Postgres are working.

Capture the **signup_id** — every step below uses it.

---

## Step 2 — Force Sandbox → Launch (bypass Paddle)

### SAFE_MODE first

```bash
SIGNUP_ID=42       # from step 1
TOKEN=$HATCHIK_ADMIN_TOKEN

curl -X POST "https://hatchik.com/api/admin/promote-to-launch?signup_id=$SIGNUP_ID" \
  -H "X-Admin-Token: $TOKEN"
```

Response:
```json
{
  "ok": true,
  "signup_id": 42,
  "event_id": "admin-force-launch-1700000000",
  "mode": "safe",
  "note": "Promote subprocess queued. Watch the journal: ..."
}
```

This records a `tier_transitions` row (sandbox→launch, note=admin force-promote)
and fires `promote.py --signup-id 42` as a detached subprocess. In
SAFE_MODE the script emails the founder a plan but does NOT call
Hetzner / Cloudflare / Porkbun / Infomaniak — nothing actually changes
in the world. Read the plan email to confirm the orchestrator
understands the intent.

### Real provisioning

Once the SAFE_MODE plan looks right, drop SAFE_MODE:

```bash
curl -X POST "https://hatchik.com/api/admin/promote-to-launch?signup_id=$SIGNUP_ID&execute=1" \
  -H "X-Admin-Token: $TOKEN"
```

`execute=1` sets `HATCHIK_PROMOTE_EXECUTE=1` in the subprocess env,
which `promote.py` reads to drop SAFE_MODE. It will:

- (If domain new) call Porkbun to register the domain
- Call Hetzner to spin a CAX31 / find a CAX41 slot
- SSH-bootstrap the substrate
- Migrate the Sandbox DB → new host
- Flip DNS via Cloudflare
- Wire Infomaniak mailboxes (`hello@`, `support@`, `noreply@`)
- Email customer + founder

Watch progress:
```bash
journalctl -u hatchik-signup -f
# In a second pane:
ssh root@launch-host journalctl -u promote@$SIGNUP_ID -f
```

When done, the registry rolls forward:
```bash
ssh root@orchestrator-host cat /opt/hatchik-launch-orchestrator/registry.json
```

---

## Step 3 — Force Launch → Growth (bypass 15-signup check)

### Option A — admin force endpoint

```bash
curl -X POST "https://hatchik.com/api/admin/promote-to-growth?signup_id=$SIGNUP_ID" \
  -H "X-Admin-Token: $TOKEN"
```

SAFE_MODE again by default. Drops straight into
`promote_to_growth.py --signup-id $SIGNUP_ID` (which has no count
check — `auto_graduate.py` does, but we skip that). Real run:

```bash
curl -X POST "https://hatchik.com/api/admin/promote-to-growth?signup_id=$SIGNUP_ID&execute=1" \
  -H "X-Admin-Token: $TOKEN"
```

`promote_to_growth.py --execute` will:

- Provision a dedicated CAX31 in the customer's region
- SSH-bootstrap the substrate on it
- pg_dump + 3-hop rsync the Launch DB → new host
- Flip DNS to the new IP
- Decommission the shared Launch slot
- Mark the registry tier=growth
- Email the customer ("you crossed 15 signups, you're on Growth")

### Option B — exercise the real `auto_graduate.py` path

If you want to test the auto-graduation logic too, lower the threshold
in the orchestrator's env and let the daily timer (or a one-shot manual
run) trigger it:

```bash
# On the orchestrator host (/etc/hatchik/orchestrator.env):
HATCHIK_AUTO_GRADUATE_USER_COUNT=2

# Then on the customer's Launch host, register two test end-users:
docker exec alphatest-postgres-1 psql -U postgres -tAc \
  "INSERT INTO auth.users (email) VALUES ('t1@x.com'), ('t2@x.com');"

# Run the sweep:
ssh root@orchestrator-host \
  python3 /opt/hatchik/launch-orchestrator/auto_graduate.py --slug alphatest --json
```

This is the more honest test — it actually exercises the user-count
query + decision logic. The admin endpoint above just short-circuits to
the promotion subprocess.

---

## What to watch for at each step

| Step | What you should see |
|---|---|
| 1 | New row in `signups` table, status `live-sandbox`, sandbox URL responds 200, GitHub repo created, Resend log line (or email) |
| 2 | New row in `tier_transitions` (`sandbox`→`launch`), Hetzner CAX41 slot allocated OR new CAX31 spawned, DNS A-record updated on `alphatest.hatchik.com`, Infomaniak mailboxes appear, status `live-launch` |
| 3 | New row in `tier_transitions` (`launch`→`growth`), new dedicated CAX31 in the registry, DB rows match between old + new, DNS points to new IP, old shared slot decommissioned, status `live-growth` |

Every admin call records an `mcp_audit_log` row visible at
`/account → Activity`.

---

## Rollback

The admin endpoints don't have a one-shot undo (the underlying
state changes are irreversible — you can't unspawn a CAX31 cheaply).
If a `execute=1` run goes sideways:

1. **Sandbox → Launch:** decommission the new Launch tenant with
   `decommission.py --slug alphatest --hard`, then reset the signups row
   to `tier='sandbox', status='live-sandbox'`. The original Sandbox is
   still alive (promote.py keeps it as a dev environment).
2. **Launch → Growth:** rsync direction is irreversible. The cleanest
   "rollback" is to decommission the new Growth host and treat the old
   Launch as canonical (if it was kept alive); otherwise restore from
   the Launch nightly snapshot.

Most things go sideways in SAFE_MODE first — keep `execute=0` for the
dry run.

---

## Quick reference

```bash
# Sandbox creation
curl https://hatchik.com/api/signup -H 'Content-Type: application/json' \
  -d '{"email":"...","product_name":"...","tier":"sandbox","accepted_terms":true}'

# Force Launch (SAFE_MODE)
curl -X POST 'https://hatchik.com/api/admin/promote-to-launch?signup_id=42' \
  -H "X-Admin-Token: $HATCHIK_ADMIN_TOKEN"

# Force Launch (real provisioning)
curl -X POST 'https://hatchik.com/api/admin/promote-to-launch?signup_id=42&execute=1' \
  -H "X-Admin-Token: $HATCHIK_ADMIN_TOKEN"

# Force Growth (SAFE_MODE)
curl -X POST 'https://hatchik.com/api/admin/promote-to-growth?signup_id=42' \
  -H "X-Admin-Token: $HATCHIK_ADMIN_TOKEN"

# Force Growth (real)
curl -X POST 'https://hatchik.com/api/admin/promote-to-growth?signup_id=42&execute=1' \
  -H "X-Admin-Token: $HATCHIK_ADMIN_TOKEN"
```
