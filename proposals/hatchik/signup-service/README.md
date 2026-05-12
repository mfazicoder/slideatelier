# Hatchik signup service

The concierge-MVP signup endpoint. Receives form submissions from
hatchik.com, logs them to SQLite, emails the founder, and acknowledges
the customer.

Retired once the wizard + provisioning worker ship.

## Deploy to the Infomaniak VPS

From your local machine:

```bash
# Copy the service files to the VPS
rsync -avz signup-service/ root@83.228.247.210:/opt/loftik-signup/
```

SSH to the VPS:

```bash
ssh root@83.228.247.210

# Install dependencies
cd /opt/loftik-signup
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Create the DB directory
mkdir -p /var/lib/loftik
chown www-data:www-data /var/lib/loftik

# Install the systemd unit
cp loftik-signup.service /etc/systemd/system/
# Edit /etc/systemd/system/loftik-signup.service to fill in:
#   - RESEND_API_KEY (from resend.com dashboard)
#   - LOFTIK_FOUNDER_EMAIL (your real inbox)

systemctl daemon-reload
systemctl enable --now loftik-signup
systemctl status loftik-signup
```

Then update the Caddyfile site block for hatchik.com to include:

```caddy
handle /api/signup* {
    reverse_proxy localhost:8090
}
handle /healthz {
    reverse_proxy localhost:8090
}
```

Reload Caddy:

```bash
caddy reload --config /etc/caddy/Caddyfile
# Or, if running in Docker:
# docker exec <caddy-container> caddy reload
```

## Test

```bash
# From your local machine
curl -X POST https://hatchik.com/api/signup \
    -H 'Content-Type: application/json' \
    -d '{
      "email": "test@example.com",
      "product_name": "TestApp",
      "description": "a test signup",
      "tier": "sandbox"
    }'
```

Expected: HTTP 201, `{"ok": true, "message": "Thanks. We're setting your Hatchik up..."}`

Check:
- `journalctl -u loftik-signup -f` shows the request
- SQLite has the row: `sqlite3 /var/lib/loftik/signups.db "SELECT * FROM signups"`
- Your inbox got the founder notification
- The test email address got the customer acknowledgement

## Read signups

The simplest way during the concierge phase:

```bash
ssh root@83.228.247.210
sqlite3 /var/lib/loftik/signups.db <<'EOF'
.headers on
.mode column
SELECT id, created_at, email, tier, product_name, status FROM signups WHERE status = 'new' ORDER BY created_at DESC;
EOF
```

Mark as in-progress / done once you start provisioning:

```bash
sqlite3 /var/lib/loftik/signups.db "UPDATE signups SET status = 'provisioning' WHERE id = 1"
sqlite3 /var/lib/loftik/signups.db "UPDATE signups SET status = 'live' WHERE id = 1"
```

## Stats endpoint (public)

`GET https://hatchik.com/api/signup/stats` returns `{"total": N, "new": M}`.

The marketing page can pull this if you want a real signup counter
on the site. Don't show the count if it's <10 (looks anaemic); start
displaying when there are 10+ paying customers.

## Why not Tally / Buttondown / etc.

You can swap to a hosted form later. SQLite + Resend on your own VPS
gives you:

- Full data ownership (signups are in your DB, not in someone else's
  cloud)
- No vendor account to manage
- Zero recurring cost (Resend free tier covers 3,000 emails/month
  before you'd pay)
- Same infrastructure as the rest of Hatchik internal — one fewer
  thing to migrate later

## When this gets retired

When the wizard ships:

1. Wizard's POST endpoint takes over the role of this service
2. Existing rows in `signups.db` get migrated to the wizard's
   `wizard_sessions` table
3. systemd unit stopped; this service archived
4. Caddy `/api/signup` block deleted (wizard handles its own paths)

The SQLite DB stays as a historical record of the concierge cohort
— useful for cohort analysis later.
