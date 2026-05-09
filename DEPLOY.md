# slideAtelier — Production Deploy Runbook

The shipped target is **self-hosted on a VPS** (Infomaniak / Hetzner /
DigitalOcean — anything that gives you docker + a public IP). The
**Fly.io** and **Render** configs are kept as appendices for future use
under `deploy/alternates/`.

---

## Self-hosted VPS — the recommended path

For full step-by-step instructions including the Supabase stack and the
private-beta invite gate, read **`HANDOFF.md`** at the repo root. Quick
recap below.

### Prerequisites

1. A VPS with Docker + the Compose v2 plugin. (`apt install docker.io
   docker-compose-plugin` on Ubuntu/Debian.)
2. An Anthropic API key with billing enabled.
3. (Optional, recommended once it lands) A domain pointing at the VPS
   for Let's Encrypt.

### One-time setup

```bash
ssh root@<vps-ip>
git clone <slideatelier-repo-url>
cd slideatelier
cp .env.example .env
cp supabase/.env.example supabase/.env
# Edit BOTH .env files. See HANDOFF.md for the full var cheat sheet.
chmod +x scripts/smoke_test_deploy.sh
```

### Bring it up

```bash
docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d
docker compose logs -f slideatelier caddy   # tail until 'Application startup complete'
```

### Mint the first invite + sign up

```bash
docker compose exec slideatelier .venv/bin/python -m slideatelier.cli \
  invite create --max-uses 1 --expires-days 14
# → printed code, e.g. AbcDef123_-
```

Browse to `https://<vps-ip>/login`, accept the self-signed cert warning
(when running with `Caddyfile.ip-only`), click "Create account", paste the
code.

### Smoke test

```bash
./scripts/smoke_test_deploy.sh --insecure --invite=<code> https://<vps-ip>
```

### Switching to a real DNS + Let's Encrypt cert

When DNS resolves to the VPS:

1. Set `DOMAIN=slideatelier.example.com` and `ACME_EMAIL=you@example.com`
   in `.env`.
2. Set `CADDY_CONFIG=/etc/caddy/Caddyfile` (drop the `.ip-only` suffix).
3. `docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d caddy`

Caddy automatically requests + renews the cert.

### Healthcheck reference

- `GET /api/ready` — alive; safe to poll every few seconds.
- `GET /api/health` — verifies `output/` writable + library catalog parses.
  Returns **503** if degraded so a load balancer pulls it out of rotation.

The deep check intentionally does NOT call Anthropic.

### Rollback

```bash
git -C /root/slideatelier checkout <previous-good-sha>
docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d --build
```

---

## Common failures (VPS)

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| App exits with code 2 at boot | Required env var missing in `production` mode | Read the `startup_validation_failed` log line; set the listed vars |
| `/api/health` returns 503 with `output_writable.ok=false` | Volume not writable | `docker compose exec --user 0 slideatelier chown -R 10001:10001 /app/output` |
| TLS handshake fails with LE Caddyfile | DNS hasn't propagated, or port 80 blocked | `dig $DOMAIN`; check firewall; or fall back to `Caddyfile.ip-only` |
| `ANTHROPIC_API_KEY not set` in generation logs | API key wasn't passed at runtime | Re-set in `.env` and `docker compose restart slideatelier` |
| `supabase-realtime` crash-loop on `key must be 16 bytes` | `REALTIME_DB_ENC_KEY` wrong length | Set to **exactly 16 ASCII chars** |

For more, see `HANDOFF.md`.

---

## Appendix A — Fly.io (alternate)

Configs live at `deploy/alternates/fly.toml`. Use these only if you're
not deploying to your own VPS.

### One-time setup

```bash
brew install flyctl
fly auth login
fly apps create slideatelier        # or change `app =` in fly.toml first
```

### Set secrets

```bash
fly secrets set \
  ANTHROPIC_API_KEY=sk-ant-... \
  DOMAIN=slideatelier.example.com \
  SESSION_SECRET="$(openssl rand -hex 32)" \
  ACME_EMAIL=you@example.com
```

### DNS + deploy

```bash
fly ips allocate-v4
fly ips allocate-v6
fly ips list
# Add A + AAAA records at your DNS provider, then:
fly deploy -c deploy/alternates/fly.toml
```

### Verify

```bash
fly status
curl -fsS https://$DOMAIN/api/ready
fly logs
```

---

## Appendix B — Render (alternate)

Config at `deploy/alternates/render.yaml`. In the Render dashboard:
**New → Blueprint**, connect the GitHub repo, point it at
`deploy/alternates/render.yaml`. Approve the proposed `slideatelier` web
service + 1 GB disk. Set `ANTHROPIC_API_KEY`, `DOMAIN`, `ACME_EMAIL` in
the service's **Environment** tab. Add the custom domain under
**Settings → Custom Domain**. Pushing to the tracked branch auto-deploys.

---

## Healthcheck reference (all targets)

- `GET /api/ready` — alive.
- `GET /api/health` — degraded checks → 503.

Neither touches Anthropic.
