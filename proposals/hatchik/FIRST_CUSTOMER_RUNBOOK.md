# Hatchik — First-Customer Runbook

For the concierge-MVP phase. Sandbox tier is now **fully automated** by
the orchestrator on the sandbox host — see `sandbox-orchestrator/` for
internals. Launch tier remains manual until the cross-region
provisioning worker is built. End to end: ~5 minutes per Sandbox
customer (mostly waiting for DNS + container boot), 45–90 minutes per
Launch customer.

The goal during this phase is to deliver the same experience the
automated pipeline will eventually deliver. Customers shouldn't feel
they're a beta; they should feel they're hand-shaped.

---

## Infrastructure summary (where things live)

Everything Sandbox-tier runs on one VPS — the **sandbox host** at
`178.105.139.144` (Hetzner CAX21, Nuremberg). On that box:

- **Apex `hatchik.com`** — static marketing files at `/var/www/hatchik`,
  served by host Caddy with wildcard TLS
- **Signup API `hatchik.com/api/signup`** — `hatchik-signup.service`
  (systemd, FastAPI on `127.0.0.1:8090`) at `/opt/hatchik-signup/`
- **Sandbox orchestrator** — `provision.py` at
  `/opt/hatchik-orchestrator/` (called by signup-service via subprocess)
- **Per-tenant compose stacks** — `/opt/hatchik-orchestrator/sandboxes/<slug>/`,
  each bound to a unique localhost port 18000–18099
- **Host Caddy** — `/opt/hatchik-host-caddy/` (compose stack with
  wildcard cert + `import tenants.d/*.caddy` for per-tenant routes)

The Resend `from` header is set in `/etc/hatchik-signup.env` as
`HATCHIK_FROM_EMAIL=Hatchik <noreply@hatchik.com>`. The display-name
prefix (`Hatchik <...>`) is what Gmail shows in the inbox sender column;
without it Gmail extracts the local-part and shows `noreply`. After any
change here, `systemctl restart hatchik-signup`.

## Trigger

A signup form submission posts to `https://hatchik.com/api/signup`. The
signup-service:

1. Records the signup in `/var/lib/hatchik/signups.json`
2. Sends the welcome acknowledgement email via Resend
3. For Sandbox tier: subprocess-spawns `provision.py <email> <slug>
   <product>` which mints JWTs, renders the substrate, writes a tenant
   Caddyfile, runs `docker compose up`, reloads host Caddy, and emails
   the customer when their sandbox is live

Payload contains:

- Customer email
- Product name / description (what they want to build)
- Tier (Sandbox / Launch)
- For Launch: Paddle payment confirmation, region preference, domain
  preference (BYO existing or new)

## Within 1 hour — Acknowledge

Reply personally. Don't use a canned template raw — open with their
name and a sentence that proves you read what they sent.

Use the template at `WELCOME_EMAILS.md §1` as your skeleton. Aim for
3–4 sentences. Set expectation: "I'll have your Hatchik live within
24h. I'll email you again when it's ready."

If Launch tier: the £79 charge is in Stripe. Confirm payment landed,
note the amount and customer ID in your tracking sheet.

## Provisioning — Sandbox tier (free, automated)

The orchestrator handles steps 1–6 automatically when the signup
arrives. Your job is to verify it worked and send a personalised
welcome on top of the automated emails.

1. **Verify provisioning landed.** SSH into the sandbox host
   (`ssh -i ~/.ssh/hatchik-deploy root@178.105.139.144`) and check:
   ```bash
   tail -20 /var/lib/hatchik/signups.json    # confirm signup was recorded
   ls /opt/hatchik-orchestrator/sandboxes/    # confirm tenant directory exists
   docker ps --format "{{.Names}}" | grep <slug>    # confirm 9 containers up
   curl -sk -o /dev/null -w "%{http_code}\n" https://<slug>.hatchik.com/
   ```
2. **Smoke-test the sandbox:** sign-up flow works, login works,
   Supabase Studio accessible at `/studio`, the static marketing copy
   on `/` shows `{{PRODUCT_NAME}}` substituted to the customer's name.
3. **If provisioning failed** (no containers, 404 from `<slug>.hatchik.com`):
   re-run manually:
   ```bash
   cd /opt/hatchik-orchestrator
   python3 provision.py <email> <slug> "<product description>"
   ```
   Watch the log for the failing step. Most failures are DNS
   propagation (transient) or substrate boot bugs (fix in the local
   template, rsync to host).
4. **Send the welcome email** (§4 below) — personalise on top of the
   automated "your sandbox is live" Resend email.

Time: 5 minutes if the orchestrator worked, ~15 if you need to debug.

## Provisioning — Launch tier (paid)

1. **Provision a fresh VPS in the customer's region**
   - UK/EU customer → Hetzner CPX11 in Falkenstein or Helsinki (use
     Hetzner Cloud Console or `hcloud server create`)
   - US East → DigitalOcean Basic 2GB in NYC
   - US West → DO Basic 2GB in SFO
   - Singapore → Vultr Cloud Compute 2GB
   - Note the new VPS IP

2. **Register or configure the domain**
   - If customer is bringing their own domain: add it as a Cloudflare
     zone in your Cloudflare account, give them the nameservers to
     update at their registrar
   - If they need a new domain: register via Infomaniak Domain Manager
     or Cloudflare Registrar in the customer's name (use their email +
     a payment method they've authorised — typically the same Stripe
     card via Stripe Connect, or invoice separately)
   - Add an A record pointing the domain to the new VPS IP

3. **Bootstrap the VPS**
   ```bash
   ssh root@<new-vps-ip>

   # Install Docker + docker-compose plugin
   curl -fsSL https://get.docker.com | sh
   apt update && apt install -y caddy git python3-venv

   # Clone the substrate template
   mkdir -p /opt/hatchik
   cd /opt/hatchik
   git clone /opt/hatchik/substrate-template-mirror <customer-slug>
   # (or scp the substrate-template directory from your dev machine
   # since the git repo isn't pushed anywhere public yet)
   ```

4. **Generate `.env` with real values**
   - `PRODUCT_NAME` = customer's product name
   - `DOMAIN` = customer's domain
   - `JWT_SECRET` = `openssl rand -hex 32`
   - `POSTGRES_PASSWORD` = `openssl rand -hex 24`
   - `STRIPE_*` = customer's Stripe Connect keys (if they've connected
     Stripe; otherwise leave empty for now and update later)
   - `RESEND_API_KEY` = your Resend account key (you can issue a
     subkey scoped to their domain)

5. **Set up the customer's mailboxes**
   - Log into Infomaniak Mail console
   - Add the customer's domain
   - Create 5 inboxes: `hello@`, `support@`, `noreply@`, `billing@`,
     plus one of the customer's choice
   - Set MX records on Cloudflare DNS
   - Configure SPF, DKIM, DMARC (Infomaniak Mail provides the values;
     paste into Cloudflare DNS)
   - Send the customer their mailbox credentials in a separate email

6. **Set up GitHub repo**
   - In your GitHub Organization (or a dedicated "Hatchik-customers"
     org), create a new private repo `customer-<slug>`
   - Push the customer's substrate (with `.env` excluded)
   - Add the customer as an owner / admin of the repo via their email
   - Send them the invite

8. **Configure Caddyfile and start the stack**
   ```bash
   cd /opt/hatchik/<customer-slug>
   # Uncomment the production block in Caddyfile, fill in {{DOMAIN}}
   docker compose up -d
   ```

9. **Smoke test** the deployed app at `https://<customer-domain>`

10. **Send the "your Hatchik is live" email** (§5 below)

Time: 45–90 minutes for the first one; 30–45 once you've done it
three times.

## Account harness — create + delete

Three layers, all live as of commit history (see `decommission.py`,
admin/self-serve endpoints in `signup-service/main.py`).

### 1. Self-serve (what customers see)

- **Sign up:** the form on `https://hatchik.com/#signup` posts to
  `POST /api/signup`. Provisioning is automatic.
- **Delete:** the footer link or
  `https://hatchik.com/delete-sandbox` posts to
  `POST /api/account/request-deletion {email}`. Customer receives a
  one-time confirmation link valid 24h. Clicking the link fires
  `GET /api/account/confirm-deletion?token=...` which subprocess-runs
  `decommission.py <slug> --hard` — tears down containers + volumes,
  removes the registry entry, and DELETEs the signup row. Anti-
  enumeration: the request endpoint always returns 202, never reveals
  whether the email matched.

### One active Sandbox per email

`POST /api/signup` enforces a single active Sandbox-tier tenant per
email. If a customer tries to create a second Sandbox while one is
still live (signup row status NOT IN
`deleted/cancelled/archived_purged` AND registry status NOT
`decommissioned`), the endpoint responds with **409 Conflict**:

```json
{
  "ok": false,
  "error": "sandbox_exists",
  "message": "You already have a Sandbox running at https://<slug>.hatchik.com. Delete it (https://hatchik.com/delete-sandbox) first if you want to start fresh."
}
```

Both the wizard at `/start` and the inline form on the marketing page
render this as a red banner with a one-click link to `/delete-sandbox`.
After the customer confirms deletion, the row flips inactive and a fresh
sign-up succeeds.

The cap **does not** apply to Launch/Growth tier — those can have
multiple per email (one per product the customer is launching).

### 2. Admin API (HTTP)

Header `X-Admin-Token: $HATCHIK_ADMIN_TOKEN` is required (the token
lives in `/etc/hatchik-signup.env` on the sandbox host).

- `GET /api/admin/accounts` — list all signups + joined tenant state
  (url, status, port).
- `DELETE /api/admin/account/{slug}` — soft decommission (keeps
  registry + signup row for audit). Append `?hard=true` to fully purge.
- `GET /api/admin/metrics/cohorts?granularity=week|month&since=YYYY-MM-DD`
  — per-cohort funnel breakdown (signups, currently live, conversion to
  Launch, mean days-to-upgrade, churn rates).
- `GET /api/admin/metrics/funnel` — all-time funnel rollup
  (sandbox→launch %, launch→growth %, overall churn %).
- `GET /api/admin/metrics/distribution` — current tier distribution
  across live tenants.

### 3. Admin CLI (`decommission.py`)

For when you're SSH'd into the sandbox host. Same teardown logic that
the API endpoints subprocess, but interactive:

```bash
python3 /opt/hatchik-orchestrator/decommission.py --list
python3 /opt/hatchik-orchestrator/decommission.py <slug>          # soft
python3 /opt/hatchik-orchestrator/decommission.py <slug> --hard   # purge
python3 /opt/hatchik-orchestrator/decommission.py --signup <id>   # lookup-by-id
```

### Metrics dashboard

Cohort-funnel dashboard at `https://hatchik.com/admin/dashboard`
(static page, served by Caddy from the marketing site). Paste your
`HATCHIK_ADMIN_TOKEN` into the header input once — it's stored in
localStorage so subsequent visits skip the prompt. Click Refresh to
pull fresh numbers; auto-refresh is intentionally disabled.

What the dashboard surfaces:

- **Top-line tiles** — total signups, currently live tenants, all-time
  Sandbox→Launch conversion %, all-time churn %.
- **Cohort table** — one row per weekly (or monthly, via toggle)
  cohort. Click a column header to sort.
- **Cohort funnel chart** — bar chart of each cohort's Sandbox → Launch
  → Growth progression (Chart.js, served from jsDelivr CDN).
- **Tier distribution donut** — how live tenants split across Sandbox /
  Launch / Growth today.

Backed by three admin API endpoints (see §2 above). All three accept
an optional `?since=YYYY-MM-DD` filter. The dashboard hits each
endpoint in parallel on every Refresh — fail-gracefully on empty DB
("no cohort data yet — come back after signup #1").

These numbers are what we use to **retire the assumed conversion /
churn figures from `MARKETING_PLAN.md` §7** by the time we hit signup
#50.
## Idle-archive lifecycle

The customer-facing copy on hatchik.com promises "archived if idle 30
days". `sandbox-orchestrator/lifecycle.py` enforces that. The daily
cadence:

| Day | Action | Customer sees |
|---|---|---|
| 0 | Provisioned, status=`live` | Sandbox ready email |
| 23 | Polite warning sent (if no sign-in since) | "Your sandbox is heading for archive" + magic-link |
| 29 | Firmer reminder | "Tomorrow we archive" + fresh magic-link |
| 30 | Archived: containers stopped, volumes snapshot to `/var/hatchik-archive/<slug>/`, Caddy route removed, status=`archived` | "Your sandbox has been archived" + restore form link |
| 30+7 | Purged: snapshots + tenant dir removed, status=`purged`, signup row status=`archived_purged` | "Your sandbox data has been deleted" |

"Idle" = no Supabase auth activity (max of `last_sign_in_at` and
`created_at` on `auth.users`). Any sign-in resets the clock — the
reconciler re-probes activity on every run.

### Install the timer (one-time)

```bash
ssh -i ~/.ssh/hatchik-deploy root@178.105.139.144
cd /opt/hatchik-orchestrator   # where provision.py + decommission.py + lifecycle.py + restore.py live
install -m 0644 hatchik-lifecycle.service /etc/systemd/system/
install -m 0644 hatchik-lifecycle.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hatchik-lifecycle.timer
systemctl list-timers hatchik-lifecycle.timer    # next firing should be ~02:00 UTC
```

The service reads `/opt/hatchik-orchestrator/.env` (same file
`provision.py` uses) for `RESEND_API_KEY` + `HATCHIK_FROM_EMAIL` +
`HATCHIK_FOUNDER_EMAIL`. No new env vars needed.

### Manually run a reconcile

```bash
# Dry-run: print what *would* happen, no state changes
python3 /opt/hatchik-orchestrator/lifecycle.py --dry-run --json

# Real run, single tenant (faster + safer when debugging)
python3 /opt/hatchik-orchestrator/lifecycle.py --slug <slug> --json

# Full run (same as the timer)
python3 /opt/hatchik-orchestrator/lifecycle.py
```

### Manually trigger archive / restore

Archive happens automatically on day 30. To force-archive earlier (e.g.
customer asked to put it on ice without losing data):

```bash
# Set the fake-now env to a date >= 30 days after provisioning
HATCHIK_LIFECYCLE_FAKE_NOW=2030-01-01T02:00:00Z python3 /opt/hatchik-orchestrator/lifecycle.py --slug <slug>
```

To restore an archived sandbox (within the 7-day grace window):

```bash
# CLI
python3 /opt/hatchik-orchestrator/restore.py <slug>

# Or via the admin API
curl -X POST https://hatchik.com/api/admin/account/<slug>/restore \
  -H "X-Admin-Token: $HATCHIK_ADMIN_TOKEN"
```

`restore.py` re-extracts the snapshot, brings up the compose stack,
re-mints a magic-link, and emails the customer "your sandbox is back".

### Customer restore-request flow

Customers fill `https://hatchik.com/restore-sandbox` (or reply to the
archival notice email). The form POSTs to `/api/account/request-restore`
which emails the founder with the matching archived slug(s). The
founder reviews, then runs `restore.py <slug>` on the host (or POSTs to
the admin endpoint above). Restores are **gate-kept** by an admin
because archived snapshots are valuable to spammers if abused — no
self-serve restore by clicking an email link.

### When something goes wrong

- **Reconciler crashed mid-run**: it's idempotent — re-run. Any tenant
  it already archived will be skipped on the next pass (their
  registry status is now `archived`).
- **Archive failed but the email went out**: check
  `journalctl -u hatchik-lifecycle.service -e`. If the volume snapshot
  is missing in `/var/hatchik-archive/<slug>/`, restore won't work —
  manually `docker compose up` from the original tenant dir if it's
  still around. We have not lost data because the archive path uses
  `docker compose stop` (not `down -v`) until *after* snapshots succeed.
- **Customer asks to restore past day 37**: the snapshots are gone.
  Honest answer: "Sorry, the snapshot is past its 7-day grace period —
  but you can sign up again at hatchik.com and we'll get you set up
  fresh in five minutes."

See `sandbox-orchestrator/LIFECYCLE_TESTING.md` for the test
procedure (fake-now env vars, collapsing the 30-day cycle to seconds
for end-to-end checks).

### Resetting the SQLite signup sequence

Soft-deleting individual rows leaves the autoincrement counter intact
(next signup gets `id = max(deleted) + 1`). To start fresh from `#1`:

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('/var/lib/hatchik/signups.db')
c.execute('DELETE FROM signups')
c.execute(\"DELETE FROM sqlite_sequence WHERE name='signups'\")
c.commit()
"
```

## GitHub handoff (Sandbox tier, automated)

Every Sandbox provisioning now creates a per-tenant private GitHub repo
and pushes the rendered substrate as the initial commit. The customer
gets two emails: the existing "your sandbox is ready" magic-link email,
and a new walkthrough email with `git clone`, the AI_CONTEXT.md prompt,
and the push-to-redeploy story.

### Required env (one-time on the sandbox host)

`/opt/hatchik-orchestrator/.env` needs:

```
HATCHIK_GITHUB_TOKEN=ghp_xxx          # classic PAT (repo + admin:org) OR
                                       # fine-grained PAT on HATCHIK_GITHUB_ORG
                                       # with: contents=write, administration=write,
                                       # members=write, metadata=read
HATCHIK_GITHUB_ORG=hatchik-sandboxes  # default; create the org on github.com
                                       # before issuing the token
```

Issue the token from a GitHub account that owns (or is admin on) the
org. After saving the env file, restart any process that subprocesses
provision.py — typically `systemctl restart hatchik-signup` is enough.

### What the customer sees

1. Wizard at `/start` shows an optional **GitHub username** field. If
   they fill it, provision.py invites them as an admin collaborator on
   the per-tenant repo (so they can clone, push, and edit). If they
   leave it blank, the repo still gets created under
   `github.com/<HATCHIK_GITHUB_ORG>/<slug>` and they can request
   collaborator access from `/account` Settings → Connect GitHub.
2. After the sandbox is live, they receive two emails: sandbox-ready
   (sign-in magic link) and the walkthrough (clone + Claude Code prompt).
3. In `/account` Settings → Connect GitHub they can change their handle
   at any time. The change applies to **future** repos — existing repo
   collaborators have to be added manually via the GitHub UI.

### AI_CONTEXT.md

The per-tenant repo always contains `AI_CONTEXT.md` at the root —
generated by provision.py during render_substrate. Contents:

- Sandbox URL + Supabase URL (same value; Caddy fronts both)
- Supabase anon key (safe to expose; pulled from tenant `.env`)
- Repo layout: edit `apps/web/src/product/` and `apps/api/src/product/`;
  don't touch substrate files
- First-prompt template the customer can paste into Claude/Cursor
- A "Deploying changes" section that gives the AI tool both the
  `git push` flow and a direct `curl -X POST` with the per-tenant
  `deploy_token` so it can ship code without a human in the terminal
- A tip line at the very top ("when the human asks you to ship,
  push or POST — don't ask them to open a terminal") aimed squarely
  at AI tools reading the file

**No secrets in AI_CONTEXT.md.** The service-role JWT and Postgres
password stay in `.env`, which `github_repo.py` adds to `.gitignore`
before the initial commit.

### What to do when HATCHIK_GITHUB_TOKEN isn't set

Provisioning still succeeds — the customer gets a working sandbox + the
magic-link email + the AI_CONTEXT.md file in the tenant directory on
the sandbox host. They just don't get a GitHub repo (or the walkthrough
email, which is gated on `repo_url` being present).

To repair after the fact:

```bash
# On the sandbox host
cd /opt/hatchik-tenants/<slug>
git init -b main
git remote add origin https://x-access-token:<TOKEN>@github.com/<HATCHIK_GITHUB_ORG>/<slug>.git
git add -A
git commit -m "Initial substrate from Hatchik"
git push -u origin main
# Then invite the customer manually via GitHub UI, or:
curl -X PUT \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/<HATCHIK_GITHUB_ORG>/<slug>/collaborators/<gh-handle> \
  -d '{"permission":"admin"}'
```

The customer's `.env` (with secrets) must NOT be committed — confirm
`.gitignore` includes `.env` before the first push.

### Redeploy webhook

Every Sandbox now ships with an automatic redeploy pipeline. The
substrate-side flow:

1. `provision.py` generates a per-tenant `deploy_token` (32 bytes,
   url-safe) and stores it on the registry entry alongside `slug`,
   `port`, `email`, etc.
2. `github_repo.py` registers a `push` webhook on the per-tenant repo
   pointing at `https://hatchik.com/api/tenants/<slug>/redeploy` with
   the `deploy_token` as the GitHub webhook secret.
3. The endpoint lives on the signup-service (FastAPI). Pushes trigger
   `git pull --rebase && docker compose up -d --build` inside the
   tenant directory and log every step to
   `/var/log/hatchik/redeploy-<slug>.log`.

The deploy token also appears in the tenant's `AI_CONTEXT.md`, so AI
coding tools (Claude Code, Cursor, Windsurf) can POST directly to the
redeploy endpoint without going through GitHub — useful when the
customer wants to ship an uncommitted edit.

#### Where the token lives

`/opt/hatchik-tenants/registry.json` — under
`tenants["<slug>"].deploy_token`. Treat it like a credential. Rotating
it requires regenerating the AI_CONTEXT.md in the tenant repo AND
updating the GitHub webhook secret, so prefer not to rotate without
reason.

#### Two auth modes

`POST /api/tenants/<slug>/redeploy` accepts either of:

- `X-Deploy-Token: <token>` — the token verbatim. Used by AI tools.
- `X-Hub-Signature-256: sha256=<hmac>` — HMAC-SHA256 of the raw
  request body using the same `deploy_token` as the secret. Used by
  GitHub webhooks (which cannot set arbitrary headers).

Both verify against the same per-tenant token. Auth failure: 403.
Unknown slug: 404. Archived / decommissioned tenant: 410.

#### Rate limit

Six redeploys per tenant per five-minute rolling window (configurable
via `HATCHIK_REDEPLOY_RATE_LIMIT_MAX` and
`HATCHIK_REDEPLOY_RATE_LIMIT_WINDOW_SECONDS`). Concurrent requests for
the same slug return 429 with `redeploy already in progress for this
tenant` — only one redeploy per slug at a time.

#### Manual trigger via curl (for admin debugging)

```bash
SLUG=acme
TOKEN=$(jq -r ".tenants[\"$SLUG\"].deploy_token" /opt/hatchik-tenants/registry.json)
curl -s -X POST "https://hatchik.com/api/tenants/$SLUG/redeploy" \
  -H "X-Deploy-Token: $TOKEN" | jq .
# → {"ok": true, "slug": "acme", "deployed_at": "...", "commit": "abc1234", "via": "ai-tool"}
```

On failure the response includes a `log_tail` field with the last 50
log lines so you don't have to ssh in.

#### Per-tenant redeploy log

`/var/log/hatchik/redeploy-<slug>.log` — append-only, ISO-8601
timestamped. Captures every step: auth, lock acquire, git pull start +
finish, docker compose start + finish, exit codes, and the resulting
commit. If a redeploy fails, this is the first place to look.

#### Idempotency

Re-running the same push (same commit SHA) is safe: Docker decides
whether to rebuild based on layer hashes, so a no-op push results in a
fast no-op redeploy. We do not try to detect schema migrations — if a
push includes new SQL, the substrate's existing migration runner picks
it up on container start.

## Mobile builds

Every tenant repo ships with `.github/workflows/build-mobile.yml`,
which produces an unsigned iOS IPA and Android APK on every push to
`main` that touches mobile/web/Capacitor files, or on manual
dispatch. The customer-facing entry point is
`hatchik.com/account` → Mobile tab.

### How it works end-to-end

1. Customer clicks *Build now* on the Mobile tab (or pushes a commit
   that touches `apps/mobile/`, `apps/web/`, or `capacitor.config.ts`).
2. `POST /api/account/mobile-builds/{slug}/trigger` posts a
   `workflow_dispatch` event to GitHub via the
   `HATCHIK_GITHUB_TOKEN` PAT — same token that creates the tenant
   repo at provision time. Rate-limited to 3 builds per tenant per
   hour (`HATCHIK_MOBILE_BUILD_RATE_LIMIT_MAX`).
3. GitHub Actions runs two jobs in parallel:
   - `android` on `ubuntu-latest` (JDK 17, ~5 min)
   - `ios` on `macos-latest` (Xcode, ~10–15 min)
4. Each job builds the React bundle, syncs Capacitor, archives the
   native project, and uploads the binary as a workflow artefact
   (`android-apk`, `ios-ipa`).
5. The customer downloads from the workflow run page. Artefacts
   retain for 14 days.

### Where the artefacts go

Workflow artefacts live in GitHub. Hatchik does not proxy them — the
account page links straight to the run URL and the customer downloads
from there. This keeps the signup-service out of the binary-storage
business.

### Cost

GitHub Actions on free-tier private repos: 2,000 minutes/month for
ubuntu, with macOS minutes counted at 10x. At our 3-build/hour cap
that's ~75 minutes per full build (5 ubuntu + ~10 macOS × 10 = ~105
minutes per dual build, divided by both legs in parallel ≈ 25 minutes
wall-clock). Heavy users may hit GitHub's free-tier wall — they can
either wait for the next month, or (if we want to be generous) upgrade
the repo to GitHub Pro on Hatchik's side.

### Debugging a failing build

1. Open the failing run URL (visible in `/api/account/mobile-builds/{slug}`
   response or directly on the Actions tab of the tenant repo).
2. The job log shows which step blew up. Common ones:
   - **`pnpm install --frozen-lockfile`** failing → customer added a
     dep without updating the lockfile.
   - **`pnpm build`** failing → TypeScript / lint error in their
     product code.
   - **`xcodebuild archive`** failing → usually a missing Capacitor
     plugin's Pod, fixable by re-running `pod install`.
   - **`./gradlew assembleRelease`** failing → SDK version mismatch
     or Java heap size; usually a transient runner issue.
3. If it's substrate-side, fix in `substrate-template/.github/workflows/build-mobile.yml`
   and bump the substrate pointer.

### Signed builds (customer self-serve)

The default workflow produces *unsigned* binaries. To produce signed
ones the customer adds these secrets to their tenant repo
(*Settings → Secrets and variables → Actions*) and uncomments the
signing blocks in `build-mobile.yml`:

- iOS: `APPLE_CERTIFICATE_P12`, `APPLE_CERTIFICATE_PASSWORD`,
  `APPLE_PROVISIONING_PROFILE`, `APPLE_TEAM_ID`
- Android: `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`,
  `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`

Hatchik never touches these credentials. The signing-block comments
spell out the exact names and where to paste them, and the README at
`apps/mobile/README.md` walks through the cert generation flow.

### Required env (one-time on the sandbox host)

The two endpoints (`GET /api/account/mobile-builds/{slug}` and
`POST /api/account/mobile-builds/{slug}/trigger`) reuse the existing
`HATCHIK_GITHUB_TOKEN` + `HATCHIK_GITHUB_ORG` env vars set for the
GitHub-handoff agent. The token's scopes already cover
`repo + workflow`, so no new credentials are needed. If the token
is missing the endpoints surface a graceful "Connect GitHub first"
message rather than 500-ing.

## Backlog seeding (both tiers)

Provisioning drops a `BACKLOG.md` file at the root of the tenant
repo, pre-populated with ~20 starter tasks tailored to the
customer's idea (generated by the AI prompt in
`backlog-prompt.md`). The customer's AI tool reads and updates it
like any other file — no external tracker, no lock-in. Customers
who'd rather use a proper tracker (Linear, Jira, GitHub Projects,
etc.) can wire their own; we don't make that decision for them.

## Send the welcome / activation email

Use the template at `WELCOME_EMAILS.md §3` — fill in:
- Customer's name
- Hatchik URL (sandbox subdomain or their custom domain)
- GitHub repo URL
- Login credentials (or magic link)
- Mailbox webmail URL + credentials

Include a 10-minute offer: "Reply to this email if you're stuck on
anything in the first hour — I'll jump in personally."

## Day-3 check-in

Reply on the same email thread:

> "Just checking in — anything you've tried that hasn't worked, or
> anywhere you need help? Also if you've used the BACKLOG.md with
> your AI coder yet, I'd love to hear how it went."

Customers don't expect this; it's why concierge-MVP wins early
retention.

## Day-7 follow-up

Open a row in your internal customer tracker tagged with this
customer's name. Note:
- What did they ship in 7 days?
- What did they struggle with?
- Did they ask any questions the FAQ should answer?
- Did they hit any substrate bugs?

Roll all of this back into product changes.

## Tracking sheet

Until you build a proper customer dashboard, keep a simple spreadsheet
or Airtable with one row per customer:

| Field | |
|---|---|
| Signup date | |
| Email | |
| Product name | |
| Product description | |
| Tier | Sandbox / Launch |
| Domain | |
| Region | |
| VPS IP | |
| Stripe customer ID | |
| GitHub repo | |
| Sandbox URL or live URL | |
| Status | Provisioning / Live / Cancelled |
| Last interaction | |
| Notes | |

This becomes the seed data for the eventual customer dashboard.

## Launch/Growth shell — what tenants ship with on day one

Every provisioned tenant (Sandbox, Launch and Growth) now boots with two
extra out-of-the-box surfaces on top of the existing auth + billing +
settings substrate. Customers don't have to design these from scratch —
they edit copy.

### 1. Marketing landing template (`apps/web/src/routes/index.tsx`)

Replaces the old "Hello, world. This is a freshly-provisioned SaaS"
placeholder. Pulls two values from the signup row via `provision.py`'s
template substitution:

- `VITE_PRODUCT_NAME` (was already wired) — hero headline, footer,
  copy throughout
- `VITE_PRODUCT_IDEA` (new) — hero subtitle, FAQ first answer; sourced
  from the signup's `description` column, trimmed to the first sentence
  and capped at 160 chars

Sections shipped: hero with CTA → `/login`, three feature cards (copy
templated on the product name), three-step "how it works", FAQ
accordion (3 generic SaaS questions), and a footer with privacy +
terms links.

The footer also shows a **"Built with Hatchik"** referral link on
Sandbox-tier tenants only. The flag is `VITE_BUILT_WITH_HATCHIK`,
defaulting to `"true"`; Launch/Growth provisioning flips it to
`"false"` post-deploy so paying customers don't have to display it.

### 2. End-user account dashboard (`apps/web/src/routes/account.tsx`)

A new tabbed dashboard at `/account` — separate from `/settings` (which
remains for app-level preferences). Tabs:

- **Profile** — display name (writes to `public.user_profiles`),
  email change (calls `supabase.auth.updateUser({email})`, fires the
  confirmation flow)
- **Security** — password change via `supabase.auth.updateUser({password})`,
  shows last-changed date as `user.updated_at`
- **Billing** — if `VITE_STRIPE_PUBLISHABLE_KEY` is set, "Open billing
  portal" button posts to `/api/billing/portal`. The endpoint is a stub
  (returns a placeholder URL) for the customer to wire up to Stripe
  customer portal config. If the key isn't set, the tab shows
  "Billing not configured yet — your founder will set this up before
  launching."
- **Sessions** — current session card + "Sign out from other devices"
  (uses Supabase's `signOut({scope:'others'})`). Full session listing
  needs a server-side endpoint that we haven't shipped yet.
- **Danger zone** — "Delete account" with type-your-email confirmation;
  deletes the `user_profiles` row (RLS-scoped to self), signs out, and
  redirects to `/login`. Auth-user purge needs a server endpoint —
  founders add when they need it.

A new migration `packages/db/migrations/0011_user_profiles.sql`
creates the `public.user_profiles` table (RLS-scoped, idempotent).
The header nav in `__root.tsx` adds an "Account" link when the user
is signed in.

### What customers still build themselves

- Their actual product (everything in `*/product/` directories)
- Real screenshots / pricing / testimonials on the landing page
- The Stripe customer-portal config + the
  `/api/billing/portal` implementation (currently a stub)
- Any session-listing UI beyond the current session

## When you hit problems

- **Customer paid but provisioning is taking too long**: email them
  immediately with an honest update. "Hit an unexpected issue with X,
  here's what I'm doing, you'll have your Hatchik by Y."
- **Customer wants a refund within 14 days**: Stripe dashboard → find
  payment → refund. Don't argue. Reply: "Done, refund issued. If
  there's anything specific that pushed you away, I'd love to hear it
  — feedback shapes what we build next."
- **Customer wants a refund after 14 days**: per PRODUCT_OFFERING.md
  §8, setup is non-refundable after 14 days but you can refund the
  unused portion of the most recent month. Be generous if the
  customer is unhappy — first 100 customers are evangelists or they're
  detractors; choose evangelist.

## Host capacity per sandbox

Each Sandbox-tier tenant runs the full substrate stack inside its own
compose project, with per-service `mem_limit` + `cpus` set in
`substrate-template/docker-compose.yml`. Per-tenant totals (default
caps, applied via `${SANDBOX_MEM_*}` / `${SANDBOX_CPUS_*}` with
embedded defaults):

| Service            | RAM     | CPU shares |
|--------------------|---------|------------|
| postgres           | 512 MB  | 0.5        |
| supabase-auth      | 128 MB  | 0.2        |
| supabase-rest      | 128 MB  | 0.2        |
| supabase-storage   | 128 MB  | 0.2        |
| supabase-meta      | 128 MB  | 0.2        |
| supabase-studio    | 256 MB  | 0.2        |
| api                | 256 MB  | 0.3        |
| web (Vite dev)     | 512 MB  | 0.3        |
| caddy              |  64 MB  | 0.1        |
| **Per-tenant cap** | **~1.3 GB** | **~2.2 cores** |

On the CAX21 sandbox host (8 GB / 4 cores), that's **4–5 concurrent
active sandboxes** before swap pressure kicks in. The signup service
enforces this directly via `HATCHIK_MAX_CONCURRENT_PROVISIONS=3` (one
slot reserved for host overhead + idle tenants). Excess signups land
in the SQLite queue (`signups.status = 'queued'`) and the background
worker picks them up every 5 seconds as slots free.

Launch-tier tenants run on dedicated VPSes and override each
`SANDBOX_MEM_*` / `SANDBOX_CPUS_*` in their own `.env` to lift the
limits — see substrate-template README for the exact var names.

## Cap on concierge scale

Realistically, you can hand-provision **5–15 customers per week**
before this becomes painful. Track signup rate carefully. When
weekly signup rate exceeds 5, start treating wizard + orchestrator
build as the #1 priority — see ROADMAP.md Phases 2–3.

If signups exceed 25/week, throttle Stripe Checkout (return a "queue"
state) and use the waitlist temporarily until the wizard ships. Don't
overpromise.

## What customers should never see

- Frustration / apology beyond a single sentence
- Mention that the wizard isn't built yet (it's fine for them to know
  it's hand-onboarded; it's not fine for them to feel they're getting
  a worse experience)
- Different pricing / setup process than the marketing page promises
- Inconsistencies between this and PRODUCT_OFFERING.md

When in doubt, deliver more than the marketing page promises.
