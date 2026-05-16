# Deploy Hatchik marketing site to Infomaniak VPS

Paste-ready commands to get `hatchik.com` (or your chosen domain) serving
the marketing page + waitlist endpoint from your existing Infomaniak
VPS today.

Assumes:
- You've registered the domain (hatchik.com or alternative)
- You have SSH access to the Infomaniak VPS at `83.228.247.210`
- The VPS already runs Caddy for slideAtelier / Stackr / ThreadLine /
  Nextcloud (if it doesn't, see "Caddy setup if not already installed"
  at the bottom)
- You point DNS at the VPS before requesting TLS

Time estimate: **30–45 minutes** for the marketing site alone, plus
another 20 minutes if adding a waitlist backend.

---

## Step 1 — DNS records (5 minutes)

At your domain registrar (Infomaniak Domain Manager, or whoever you
registered with), add:

```
Type    Name        Value               TTL
A       @           83.228.247.210      300
A       www         83.228.247.210      300
CNAME   *           hatchik.com           300   (for subdomains later)
```

The wildcard is optional but useful — covers `app.hatchik.com`,
`proxy.hatchik.com`, and future sandbox subdomains (`<customer>.hatchik.com`).

Verify after ~5 minutes:
```bash
dig +short hatchik.com
# expect: 83.228.247.210
```

## Step 2 — Copy the marketing page to the VPS (5 minutes)

From your local machine:

```bash
# From the worktree
cd /Users/farhan/Projects/slideAtelier/.claude/worktrees/cranky-nash-5e9af5/proposals/hatchik

# Create remote target dir
ssh root@83.228.247.210 'mkdir -p /var/www/hatchik'

# Copy the marketing page
rsync -avz index.html root@83.228.247.210:/var/www/hatchik/

# Optionally also copy proposal docs (private — not served publicly)
# (skip if you don't want them on the server at all)
```

## Step 3 — Caddy site block (10 minutes)

SSH to the VPS:
```bash
ssh root@83.228.247.210
```

Find your existing Caddyfile — usually at `/etc/caddy/Caddyfile` or
managed by Docker if you run Caddy in a container. If it's the Docker
Caddy from your existing `docker-compose.yml`, edit that Caddyfile
instead.

Add a new site block:

```caddy
# /etc/caddy/Caddyfile (or your Caddyfile)

hatchik.com, www.hatchik.com {
    root * /var/www/hatchik
    file_server
    encode gzip zstd

    # Redirect www → root
    @www host www.hatchik.com
    redir @www https://hatchik.com{uri} permanent

    # Security headers
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Frame-Options "DENY"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "geolocation=(), microphone=(), camera=()"
    }

    # Waitlist endpoint (uncomment once backend is up — see Step 5)
    # handle /api/waitlist {
    #     reverse_proxy localhost:8090
    # }
}
```

Reload Caddy:
```bash
# If running directly:
caddy reload --config /etc/caddy/Caddyfile

# If running in Docker:
docker exec <caddy-container-name> caddy reload --config /etc/caddy/Caddyfile
# or
docker compose restart caddy
```

Caddy will automatically request a Let's Encrypt certificate on first
request (since you've enabled `auto_https` by default).

## Step 4 — Verify (2 minutes)

From your local machine:
```bash
curl -I https://hatchik.com
# expect: HTTP/2 200, valid cert
```

Open `https://hatchik.com` in a browser — the marketing page should load
with the animated hero chat, all the bundle rows, pricing, FAQ.

## Step 5 — Waitlist backend (~20 minutes, choose one path)

The marketing page (after Agent 2 finishes) will POST `/api/waitlist`
with `{email: "..."}`. Pick a backend that catches and stores those.

### Path A — Tally form embed (simplest, no backend)

Replace the form with a Tally embed. Tally is free for unlimited
responses. Setup:
1. Go to [tally.so](https://tally.so) → create a form with one email field
2. Embed the form on the marketing page (replace the form HTML the
   agent generates with the Tally embed snippet)
3. Email export available; integrate with Mailchimp/Resend later

No VPS-side work needed.

### Path B — Resend Audiences (recommended)

Resend has a free Audiences feature that doubles as a waitlist mailing
list. Setup:
1. Go to [resend.com/audiences](https://resend.com/audiences) → create
   an "Hatchik waitlist" audience
2. Get your Resend API key
3. Run a tiny FastAPI service on the VPS that POSTs to Resend's
   `/audiences/{id}/contacts` endpoint

Tiny FastAPI service (~20 lines):

```python
# /opt/hatchik-waitlist/main.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
import httpx
import os

app = FastAPI()
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
AUDIENCE_ID = os.environ["RESEND_AUDIENCE_ID"]

class WaitlistRequest(BaseModel):
    email: EmailStr

@app.post("/api/waitlist")
async def join_waitlist(req: WaitlistRequest):
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://api.resend.com/audiences/{AUDIENCE_ID}/contacts",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"email": req.email, "unsubscribed": False},
        )
        if r.status_code not in (200, 201):
            raise HTTPException(500, "Could not save signup")
    return {"ok": True}

@app.get("/healthz")
async def healthz():
    return {"ok": True}
```

Install + run as a systemd service:

```bash
ssh root@83.228.247.210

mkdir -p /opt/hatchik-waitlist
cd /opt/hatchik-waitlist
# (scp main.py up to here, or paste with cat > main.py)
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn[standard] httpx pydantic email-validator

cat > /etc/systemd/system/hatchik-waitlist.service <<'EOF'
[Unit]
Description=Hatchik waitlist endpoint
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/hatchik-waitlist
Environment=RESEND_API_KEY=...
Environment=RESEND_AUDIENCE_ID=...
ExecStart=/opt/hatchik-waitlist/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8090
Restart=always
User=www-data

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now hatchik-waitlist
systemctl status hatchik-waitlist
```

Then uncomment the `/api/waitlist` block in the Caddyfile and reload.

### Path C — Buttondown / EmailOctopus / Substack

Use a hosted newsletter tool that handles signups + mailing list +
unsubscribe in one. Marketing page form POSTs directly to their API.
No VPS-side backend at all.

Pick Resend if you'll later use Resend for transactional email anyway
(consistent vendor). Pick Tally if you want zero backend work. Pick
Buttondown if you want to start a newsletter from day one.

## Step 6 — Test end-to-end (5 minutes)

1. Visit `https://hatchik.com`
2. Scroll to the waitlist section
3. Enter your own email, submit
4. Check that the success message appears
5. Check Resend dashboard (or Tally / Buttondown) — your email should
   appear

Test from a different network / browser to confirm CORS / TLS / etc.
work from outside your VPS.

## Step 7 — Smoke-test before going public (10 minutes)

Browser checks:
- Hero animation cycles correctly
- All CTAs scroll to the right section
- FAQ expanders work
- Mobile view (resize browser) is readable
- All emojis render (or replace if any are broken)
- Footer links don't 404

Lighthouse audit:
```bash
# From a machine with Chrome:
npx lighthouse https://hatchik.com --view
```

Targets: Performance ≥ 90, Accessibility ≥ 95, Best Practices ≥ 90,
SEO ≥ 90.

## Step 8 — Announce (TBD; see LAUNCH_COMMS.md when Agent 2 finishes)

Don't announce until:
- Domain is verified live
- Waitlist captures emails reliably
- Privacy policy stub exists at `/privacy` (even a one-paragraph "we
  collect your email; we'll use it to email you about Hatchik; we won't
  share it" is enough for launch day)

## Caddy setup if not already installed

Skip if your VPS already runs Caddy.

```bash
# As root:
apt update && apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy

# Set the Caddyfile
cat > /etc/caddy/Caddyfile <<'EOF'
# Hatchik marketing site
hatchik.com, www.hatchik.com {
    root * /var/www/hatchik
    file_server
}
EOF

systemctl enable --now caddy
systemctl status caddy
```

## Troubleshooting

**TLS fails to provision** — check that DNS has propagated (`dig
+short hatchik.com` must return 83.228.247.210), and that port 443 is
open on the VPS firewall.

**"Site can't be reached"** — usually DNS not yet propagated. Wait 5
more minutes, then `dig` again. If your registrar has a long TTL,
flush local DNS cache.

**Caddy reload errors** — `caddy fmt /etc/caddy/Caddyfile` shows
syntax issues. `journalctl -u caddy -f` shows live logs.

**Waitlist endpoint 502** — uvicorn not running. `systemctl status
hatchik-waitlist` then `journalctl -u hatchik-waitlist -f`.

## Rollback

If anything goes wrong:
1. Remove the new Caddy site block
2. `caddy reload`
3. Delete `/var/www/hatchik`
4. Remove DNS records at the registrar
5. Existing slideAtelier / Stackr / ThreadLine / Nextcloud sites
   unaffected throughout — the new block doesn't touch them
