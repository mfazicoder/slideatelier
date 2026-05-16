# Hatchik Sandbox — pre-launch smoke test

Run this on the **sandbox host** (`178.105.139.144`, Hetzner CAX21) before
declaring the Sandbox tier publicly open. End-to-end checks everything that
must work for a real signup. ~15-20 minutes if nothing is broken.

If any step fails: stop, fix, re-run from the top. Do not skip steps.

---

## 0. Host preflight (2 min)

```bash
ssh root@178.105.139.144

# All three services up
systemctl is-active hatchik-signup hatchik-host-caddy
systemctl is-active hatchik-lifecycle.timer

# Disk + memory headroom
df -h /opt /var | awk 'NR<=3 || /opt|var/'   # /opt and /var both >30% free
free -m | awk 'NR==2 {print "used:", $3, "free:", $4}'   # free > 1500 MB

# Tenant count + ports in use
jq '.tenants | length' /opt/hatchik-orchestrator/registry.json
jq -r '.tenants[] | "\(.slug)\t\(.port)\t\(.status)"' /opt/hatchik-orchestrator/registry.json
```

Expected: signup-service, host-caddy active; lifecycle.timer active; free RAM
>1.5 GB; tenant count < 10 (capacity ceiling).

---

## 1. Signup → provision (5 min)

From your laptop (not the host):

```bash
# Use a real inbox you can read — the signup confirmation + sandbox-ready
# emails will land there.
curl -X POST https://hatchik.com/api/signup \
  -H 'Content-Type: application/json' \
  -d '{
    "email": "you+smoke@example.com",
    "product_name": "SmokeTest",
    "description": "smoke-test signup, delete me",
    "tier": "sandbox",
    "github_username": "<your-real-github-handle>"
  }'
```

Expected: HTTP 201, `{"ok": true, ...}`.

On the host, watch the queue worker pick it up:

```bash
journalctl -u hatchik-signup -f
# In a second pane:
sqlite3 /var/lib/hatchik/signups.db \
  "SELECT id, email, slug, status, created_at FROM signups ORDER BY id DESC LIMIT 3;"
```

Within ~3 minutes the row should transition `new → provisioning → live` and
the `slug` column should be populated.

---

## 2. Sandbox actually serves traffic (1 min)

```bash
# Replace <slug> with the slug from the row above
curl -sSL -o /dev/null -w '%{http_code}\n' https://<slug>.hatchik.com/
# Expect: 200
```

Open `https://<slug>.hatchik.com/` in a browser — you should see the
substrate template's "Hello, world" landing page wired with auth.

---

## 3. Five bug-fix surfaces (5 min)

Sign in to `https://hatchik.com/account` with the same inbox you signed up
with. Test each surface.

### 3a. Login by 6-digit code (alternative to magic-link)

On the sign-in screen, click **"Sign in with a code"** instead of the
magic-link path. Open the email, copy the 6-digit code, paste it into the
code field, submit. You should land on the account dashboard.

### 3b. Repo invite (GitHub Settings tab)

Go to **Settings → GitHub username**. If you provided a handle at signup,
the row should show ✓ and the **Repo** link in the sandbox card should
open your repo. If not — set the handle, save. A re-invite should fire;
you'll get the email within 30 s.

### 3c. Services summary tab

Click **"What's set up"** in the dashboard. You should see the per-sandbox
inventory (Postgres 512 MB, storage 128 MB, email ~100/day, mobile 3/hr,
redeploy 6/5min) — pulled live from the orchestrator. If it shows
"temporarily unavailable" the orchestrator subprocess is down; check
`journalctl -u hatchik-signup`.

### 3d. Mobile build dispatch

Click **"Mobile builds"** tab → **"Trigger build"**. The button should be
enabled (not rate-limited on first run). After ~5 minutes the row should
flip to `success` with download links. If it shows `403` in the history,
the GitHub PAT is missing `Actions: read/write` — fix in the GitHub PAT
settings page, then retry.

### 3e. Quantified inclusions page

Open `https://hatchik.com/docs/what-is-included.html`. Every entry should
show a numerical limit (e.g. "Postgres 512 MB", "3 mailboxes", "3 mobile
builds/hour"). No "TBD" or "soon" placeholders.

---

## 4. Delete-sandbox flow (2 min)

Still in the dashboard, click **"Delete this sandbox"** on the smoke-test
sandbox. Confirm. You should get an email with a one-time confirmation
link; click it. The sandbox row should flip to `decommissioned` within
~30 s and the slug should become unreachable.

```bash
# Verify on host
sqlite3 /var/lib/hatchik/signups.db \
  "SELECT slug, status FROM signups WHERE email LIKE '%+smoke%';"
# Expect: status = decommissioned

curl -sSL -o /dev/null -w '%{http_code}\n' https://<slug>.hatchik.com/
# Expect: 404 or 503 (Caddy route gone)
```

---

## 5. Lifecycle timer dry-run (1 min)

```bash
sudo python3 /opt/hatchik-orchestrator/lifecycle.py --dry-run --json | jq .
```

Expected: exit code 0, no `error` fields, lists tenants by age bucket
(`warn_day_23`, `warn_day_29`, `archive_day_30`, `purge_day_37`,
`healthy`). At least one bucket should be non-empty if you've had signups
over a week ago.

---

## 6. Pricing surface scan (1 min)

From your laptop, scan customer-facing pages for stale numbers:

```bash
for url in https://hatchik.com/ https://hatchik.com/start.html \
           https://hatchik.com/vs.html https://hatchik.com/docs/faq.html \
           https://hatchik.com/docs/what-is-included.html; do
  echo "=== $url ==="
  curl -sSL "$url" | grep -oE '£[0-9]+(\.[0-9]+)?(/(mo|month|yr|year))?' | sort -u
done
```

Expected anywhere a price appears: `£0`, `£14/mo`, `£39/mo`, `£89`
(setup), and the historical `£9` / `£24` only inside reprice-narrative
copy. No bare `£19`, no bare `£79`, no `£35`, no `£28`.

---

## 7. Done

If all seven steps cleared with no surprises, the sandbox tier is
production-ready. Open the gate (`SLIDEATELIER_INVITE_GATE=off`) or hand
out invite codes.

If any step needed a manual nudge, capture it in a follow-up issue
before you flip the gate. The first ten real customers are the only
chance to catch things this runbook missed.
