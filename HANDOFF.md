# slideAtelier — VPS Deploy Handoff

## What this is

slideAtelier is a Python 3.12 / FastAPI / HTMX / Tailwind / python-pptx
application that turns a brief into a consulting-quality slide deck. It runs
through three stages — **Storyboard → Wireframe → Hi-fi** — and emits both
native-editable `.pptx` files and a hosted Web Deck (HTML+SVG, all native
primitives, no canvas/raster). Auth + multi-tenant SQLite is shipped; users
sign up with email + password, and decks are namespaced under
`output/users/<id>/workflow/<job_id>/`.

This handoff packages everything you need to land slideAtelier on a single
**Infomaniak VPS** as a private beta. The deploy bundle is dual-purpose: it
brings up the slideAtelier app behind Caddy *and* a self-hosted Supabase
stack on the same network, ready to serve as the future identity / data
backbone. v1 of the app keeps using SQLite; Supabase is provisioned but not
yet wired (see "What's NOT yet on Supabase" below).

A private-beta invite gate is layered on top of the auth agent's signup. New
codes are minted from the CLI; signup rejects requests without a valid code.
All deploy commands are stamped into the "First-deploy checklist" — the
goal is that you can take the repo, this file, and ~30 minutes, and have a
working private beta at `https://<vps-ip>/login`.

---

## Repo state

Status table from `BACKLOG.md` (read it for full sprint detail):

| Sprint | Status |
|---|---|
| A — Native asset framework foundation | ✅ done |
| B — Library UX upgrade | ✅ done |
| C — Comprehensive font system | ✅ done |
| D — Visual manipulation of extras | ✅ done |
| E — User-created custom templates + brand kit | queued |
| F — Cross-stage history | ✅ done |
| G — More native templates (14 shapes × 4 themes) | ✅ done |
| H — Storyboard creative whiteboard | queued |
| I — Three-pronged entry chooser | queued |
| Y — Freeform text blocks + typography | ✅ done |
| J — Hosted Web Deck publishing | ✅ done |
| Z — Selective text stripping (.pptx + Web Deck SVG) | ✅ done |
| **Auth + multi-tenant SQLite** | ✅ done (parallel agent) |
| **Docker production image** | ✅ done (parallel agent) |
| **VPS deploy package (this handoff)** | ✅ this PR |
| K–X — Competitive-analysis gap-fill | queued |

Total tests pre-handoff: **118 → 125+** with `tests/test_invites.py`.

---

## Tech stack

- **Python 3.12** with `uv` for dependency management (`uv sync`).
- **FastAPI + Uvicorn** behind `--proxy-headers` so it trusts Caddy's
  X-Forwarded-* in production.
- **HTMX** for partial-page updates. **Tailwind CDN** for styling (no build
  step in v1).
- **Jinja2** templates in `src/slideatelier/web/templates/`.
- **SQLite** (`stdlib sqlite3`, no ORM) for users/sessions/decks/invites in
  `${SLIDEATELIER_OUTPUT_DIR}/atelier.db`.
- **bcrypt** (the library, NOT passlib — passlib breaks under bcrypt 5.x)
  for password hashing.
- **Anthropic Claude API** (`SLIDEATELIER_MODEL=claude-opus-4-7` default)
  via the `anthropic` SDK.
- **python-pptx** for native `.pptx` rendering. SVG renderers in
  `web_renderer.py` + `library_to_svg.py` for the Web Deck path.
- **Docker** multi-stage build → unprivileged UID 10001 runtime.
- **Caddy 2** reverse proxy, optional Let's Encrypt or internal CA.
- **Supabase self-hosted** (postgres + gotrue + postgrest + storage +
  studio + kong + realtime + meta) on the same docker network — provisioned
  but NOT YET consumed by the app.

---

## First-deploy checklist

1. **Local — clone + sanity check**
   ```bash
   git clone <slideatelier-repo-url>
   cd slideatelier
   uv sync
   .venv/bin/python -m pytest -q   # expect 125+ passing
   ```

2. **VPS — clone + env**
   ```bash
   ssh root@<vps-ip>
   apt update && apt install -y docker.io docker-compose-plugin git
   git clone <slideatelier-repo-url>
   cd slideatelier
   cp .env.example .env
   cp supabase/.env.example supabase/.env
   chmod +x scripts/smoke_test_deploy.sh
   ```

3. **VPS — fill in `.env`**
   ```env
   ANTHROPIC_API_KEY=sk-ant-...
   SLIDEATELIER_ENV=production
   DOMAIN=slideatelier.example.com   # or omit; see CADDY_CONFIG below
   ACME_EMAIL=you@example.com         # required for LE Caddyfile
   SESSION_SECRET=$(openssl rand -hex 32)

   # IP-only mode (no DNS yet):
   CADDY_CONFIG=/etc/caddy/Caddyfile.ip-only

   # Studio basicauth — generate hash:
   #   docker run --rm caddy:2-alpine caddy hash-password --plaintext 'pickapw'
   STUDIO_BASICAUTH_USER=admin
   STUDIO_BASICAUTH_HASH=$2a$14$........
   ```

4. **VPS — fill in `supabase/.env`** (see file for full list; minimum:
   `POSTGRES_PASSWORD`, `JWT_SECRET`, `ANON_KEY`, `SERVICE_ROLE_KEY`,
   `REALTIME_DB_ENC_KEY`, `REALTIME_SECRET_KEY_BASE`, `DASHBOARD_PASSWORD`).

5. **VPS — single `up` command**
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d
   ```
   First boot pulls ~10 images and brings them up in dependency order
   (postgres healthy → auth/rest/realtime/storage start → kong + studio
   join). Allow 60–90 s.

6. **VPS — generate the first invite**
   ```bash
   docker compose exec slideatelier .venv/bin/python -m slideatelier.cli \
     invite create --max-uses 1 --expires-days 14
   ```
   Copy the printed code.

7. **Browser — sign up**
   - Visit `https://<vps-ip>/login` (accept the self-signed cert warning).
   - Click "Create account", enter email + password + the invite code.
   - You land on `/workflow`. Generate a deck. Publish it. Visit
     `/web/<slug>` to confirm the public viewer renders.

8. **VPS — run the smoke test**
   ```bash
   ./scripts/smoke_test_deploy.sh --insecure --invite=<code> https://<vps-ip>
   ```

---

## Env-var cheat sheet

Every variable the app and stack read at runtime:

### Application — `.env` at project root
| Var | Required | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | yes | Claude API key. Validated on first generation, NOT at boot. |
| `SLIDEATELIER_ENV` | prod | Set to `production` to enable strict env validation + secure cookie. |
| `SLIDEATELIER_MODEL` | no | Default `claude-opus-4-7`. |
| `SLIDEATELIER_OUTPUT_DIR` | no | Default `./output` (container: `/app/output`). |
| `SLIDEATELIER_TEMPLATES_DIR` | no | Default `./templates` (container: `/app/templates`). |
| `DOMAIN` | LE only | Public hostname for Caddy + ACME. |
| `ACME_EMAIL` | LE only | Let's Encrypt contact. |
| `SESSION_SECRET` | prod | Long random string (currently unused signal but reserved). |
| `STUDIO_BASICAUTH_USER` | VPS | Caddy basicauth user for `/studio`. |
| `STUDIO_BASICAUTH_HASH` | VPS | Caddy basicauth bcrypt-style hash. |
| `CADDY_CONFIG` | no | Path to caddyfile in container; default `/etc/caddy/Caddyfile`. |

### Supabase — `supabase/.env`
| Var | Required | Purpose |
| --- | --- | --- |
| `POSTGRES_DB` | no | Default `postgres`. |
| `POSTGRES_USER` | no | Default `postgres`. |
| `POSTGRES_PASSWORD` | yes | The big one. `openssl rand -hex 24`. |
| `JWT_SECRET` | yes | Used by GoTrue + PostgREST + Realtime + Storage. ≥32 chars. |
| `JWT_EXPIRY` | no | Seconds. Default 3600. |
| `ANON_KEY` | yes | JWT signed with $JWT_SECRET, role `anon`. |
| `SERVICE_ROLE_KEY` | yes | JWT signed with $JWT_SECRET, role `service_role`. |
| `SITE_URL` | yes | The public URL of the app. |
| `SUPABASE_PUBLIC_URL` | yes | Public URL of the Kong gateway (e.g. `https://host/supabase`). |
| `ADDITIONAL_REDIRECT_URLS` | no | Comma-separated post-login redirect allowlist. |
| `DISABLE_SIGNUP` | no | `true` to lock down GoTrue signups (we use slideAtelier's invite gate). |
| `MAILER_AUTOCONFIRM` | no | `true` to skip email-confirmation in v1. |
| `SMTP_HOST/PORT/USER/PASS/ADMIN_EMAIL` | no | SMTP for password reset / confirmation. |
| `REALTIME_DB_ENC_KEY` | yes | Exactly 16 bytes — `openssl rand -hex 8`. |
| `REALTIME_SECRET_KEY_BASE` | yes | `openssl rand -hex 32`. |
| `FILE_SIZE_LIMIT` | no | Bytes. Default 50 MB. |
| `PGRST_DB_SCHEMAS` | no | Default `public,storage,graphql_public`. |
| `STUDIO_DEFAULT_ORGANIZATION` | no | Display label in Studio. |
| `STUDIO_DEFAULT_PROJECT` | no | Same. |
| `DASHBOARD_USERNAME` | no | Kong dashboard plugin user. Default `supabase`. |
| `DASHBOARD_PASSWORD` | yes | Kong dashboard plugin password. |

---

## Smoke-test instructions

```bash
# Quick health check (no signup):
./scripts/smoke_test_deploy.sh --insecure https://<vps-ip>

# Full e2e flow (requires an invite code):
./scripts/smoke_test_deploy.sh --insecure --invite=<code> https://<vps-ip>
```

The script asserts:
1. `/api/ready` returns 200.
2. `/api/health` returns 200 (output writable, library catalog parses).
3. `/login` renders.
4. With `--invite`: signup succeeds, session cookie is set, `/me` reports
   authenticated, `/workflow` is reachable.

Drop `--insecure` once you switch to the LE-driven Caddyfile.

---

## Common failures

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `caddy_1 \| http: TLS handshake error: tls: no certificates configured` | DOMAIN unset and you're using the LE Caddyfile | Either set `DOMAIN`+`ACME_EMAIL` AND point DNS at the VPS, OR set `CADDY_CONFIG=/etc/caddy/Caddyfile.ip-only` and re-`up`. |
| `slideatelier_1 \| startup_validation_failed missing=['ACME_EMAIL']` | `SLIDEATELIER_ENV=production` set without all required vars | Read the log line; set the listed vars in `.env`; `docker compose restart slideatelier`. |
| `/api/health` returns 503 with `output_writable.ok=false` | The `output` volume isn't writable by UID 10001 | `docker compose exec --user 0 slideatelier chown -R 10001:10001 /app/output`. |
| Signup form: "Invite code is required." | Auth signup wired to invites correctly, but no code passed | Generate one: `docker compose exec slideatelier .venv/bin/python -m slideatelier.cli invite create`. |
| `supabase-realtime` crash-loops with `key must be 16 bytes` | `REALTIME_DB_ENC_KEY` wrong length | `openssl rand -hex 8` (= 16 hex chars = 8 bytes? no — set to **exactly 16 ASCII chars**, e.g. `openssl rand -base64 12 \| head -c16`). |
| Browser shows "Your connection is not private" forever | Using `Caddyfile.ip-only` (self-signed); user clicked "Don't proceed" | Click "Advanced → Proceed anyway". Once DNS points at the VPS, swap `CADDY_CONFIG=/etc/caddy/Caddyfile` and restart Caddy for a real cert. |

---

## What's NOT yet on Supabase

The Supabase stack runs on the VPS, but slideAtelier still reads/writes its
own SQLite at `${SLIDEATELIER_OUTPUT_DIR}/atelier.db`. The migration triggers
to swap parts of the app onto Supabase land in these queued sprints:

- **Sprint U — Real-time collaborative editing.** The y.js / Liveblocks
  alternative is `supabase-realtime` over the postgres CDC stream. When
  Sprint U lands, `decks` and `slides` move to postgres rows so realtime
  presence + co-editing work.
- **Sprint O — Slide Collections (CMS-for-decks) + Decks API.** PostgREST
  (`/supabase/rest/v1`) becomes the headless API surface. The user-supplied
  Collections will be postgres tables exposed via PostgREST + RLS.
- **Sprint Q — Slide Analytics.** Beacon events from the Web Deck land in
  a postgres table; nightly aggregation runs as a cron-style postgres
  function.

In v1, `atelier.db` is the source of truth. The supabase containers are up
so the migration paths above don't require infrastructure work — just code.

---

## Out-of-scope but worth flagging

- **Backups.** No automated backup wiring yet. For private beta, run a
  daily `docker compose exec supabase-db pg_dumpall ...` cron and copy the
  SQLite file off-host. Sprint candidate.
- **Logs.** Default `docker compose logs` only. No log aggregation. Fine
  for private beta; revisit before a public launch.
- **Custom domain on Web Deck.** Web Deck slugs live under
  `https://<vps-ip>/web/<slug>` only. Custom domains were deferred from
  Sprint J; revisit when commercial users want vanity URLs.
- **Rate limiting outside `/login`.** `/api/jobs/*` is unrate-limited.
  Behind invite-only access this is acceptable, but consider Caddy's
  `rate_limit` plugin once you open signups.
