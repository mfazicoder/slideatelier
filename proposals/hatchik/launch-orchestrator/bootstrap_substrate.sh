#!/usr/bin/env bash
#
# bootstrap_substrate.sh — first-boot bootstrap for a freshly-provisioned
# Hatchik Launch VPS.
#
# Runs ON the new VPS, fired remotely by promote.py via SSH. By this
# point cloud-init has already installed docker.io / docker-compose-plugin
# / git / ufw / swap (see promote._cloud_init_script). This script does
# the application layer: pull the customer's repo, restore their DB
# snapshot if one exists, write the .env, and bring the stack up.
#
# Usage (on the new VPS):
#   bootstrap_substrate.sh \
#       --repo-url https://github.com/customer/repo.git \
#       --domain  customer-domain.com \
#       --sandbox-host 178.105.139.144 \
#       --sandbox-slug prepsheet            # optional; omit if no DB to migrate
#
# Idempotent. Re-running compares state and only re-does what changed.

set -euo pipefail

REPO_URL=""
DOMAIN=""
SANDBOX_HOST=""
SANDBOX_SLUG=""
APP_DIR="/opt/app"
LOG="/var/log/hatchik-bootstrap.log"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo-url)       REPO_URL="$2";       shift 2 ;;
        --domain)         DOMAIN="$2";         shift 2 ;;
        --sandbox-host)   SANDBOX_HOST="$2";   shift 2 ;;
        --sandbox-slug)   SANDBOX_SLUG="$2";   shift 2 ;;
        --app-dir)        APP_DIR="$2";        shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$REPO_URL" || -z "$DOMAIN" ]]; then
    echo "Usage: $0 --repo-url URL --domain DOMAIN [--sandbox-host IP --sandbox-slug SLUG]" >&2
    exit 1
fi

# All output to both stdout and the log so promote.py can capture it.
exec > >(tee -a "$LOG") 2>&1

echo "── bootstrap_substrate.sh started $(date -u) ─────────────────"
echo "REPO_URL=$REPO_URL"
echo "DOMAIN=$DOMAIN"
echo "SANDBOX_HOST=${SANDBOX_HOST:-(none)}"
echo "SANDBOX_SLUG=${SANDBOX_SLUG:-(none)}"
echo "APP_DIR=$APP_DIR"

# ─── 1. Wait for docker daemon (cloud-init may still be settling) ──────
echo "→ waiting for docker daemon..."
for _ in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then break; fi
    sleep 2
done
docker info >/dev/null  # final assert

# ─── 2. Clone or update the customer's repo ────────────────────────────
if [[ -d "$APP_DIR/.git" ]]; then
    echo "→ $APP_DIR exists; pulling latest"
    git -C "$APP_DIR" fetch --depth 1 origin main
    git -C "$APP_DIR" reset --hard origin/main
else
    echo "→ cloning $REPO_URL into $APP_DIR"
    git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi

# ─── 3. Write the .env (idempotent — only seed if missing) ─────────────
if [[ ! -f "$APP_DIR/.env" ]]; then
    if [[ -f "$APP_DIR/.env.example" ]]; then
        echo "→ seeding .env from .env.example (you'll need to fill in real secrets)"
        cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    else
        echo "→ creating empty .env (no template found in repo)"
        touch "$APP_DIR/.env"
    fi
fi

# Always ensure DOMAIN line is present + correct (idempotent)
if grep -qE '^DOMAIN=' "$APP_DIR/.env"; then
    sed -i.bak -E "s|^DOMAIN=.*$|DOMAIN=${DOMAIN}|" "$APP_DIR/.env"
else
    echo "DOMAIN=${DOMAIN}" >> "$APP_DIR/.env"
fi
rm -f "$APP_DIR/.env.bak"

# ─── 3b. Activate the production Caddyfile block ───────────────────────
# Substrate ships with a commented-out production block keyed on
# `{{DOMAIN}}`. We:
#   - Replace `{{DOMAIN}}` with the real domain
#   - Strip leading `# ` from the production block lines
# Idempotent: if the substitution + uncomment has already happened
# (no `{{DOMAIN}}` left in the file), this is a no-op.
if [[ -f "$APP_DIR/Caddyfile" ]] && grep -q '{{DOMAIN}}' "$APP_DIR/Caddyfile"; then
    echo "→ activating production Caddyfile block for $DOMAIN"
    # First substitute the placeholder
    sed -i.bak "s|{{DOMAIN}}|${DOMAIN}|g" "$APP_DIR/Caddyfile"
    # Then uncomment lines from "# DOMAIN {" through the matching "# }"
    # using awk so we can track nesting depth.
    awk -v dom="$DOMAIN" '
        BEGIN { in_block = 0 }
        $0 ~ "^# " dom " \\{" { in_block = 1 }
        in_block && /^# / { sub(/^# /, ""); print; if (/^\}/) in_block = 0; next }
        in_block && /^#$/  { print ""; next }
        { print }
    ' "$APP_DIR/Caddyfile" > "$APP_DIR/Caddyfile.tmp"
    mv "$APP_DIR/Caddyfile.tmp" "$APP_DIR/Caddyfile"
    rm -f "$APP_DIR/Caddyfile.bak"
fi

# ─── 4. Migrate DB from sandbox (if applicable) ────────────────────────
if [[ -n "$SANDBOX_HOST" && -n "$SANDBOX_SLUG" ]]; then
    DUMP="/tmp/sandbox-${SANDBOX_SLUG}.dump.gz"
    if [[ -f "$APP_DIR/.bootstrap-db-restored" ]]; then
        echo "→ DB already restored (sentinel exists); skipping"
    else
        echo "→ pulling DB snapshot from sandbox host"
        # The sandbox host stores per-tenant compose stacks under
        # /opt/hatchik-tenants/<slug>/. pg_dump into a tmp file there,
        # rsync it to here, then pg_restore once the local stack is up.
        ssh -o StrictHostKeyChecking=accept-new \
            "root@${SANDBOX_HOST}" \
            "docker exec hatchik-${SANDBOX_SLUG}-postgres pg_dump -U postgres -Fc -Z 6 postgres > /tmp/${SANDBOX_SLUG}.dump.gz"
        rsync -az \
            -e "ssh -o StrictHostKeyChecking=accept-new" \
            "root@${SANDBOX_HOST}:/tmp/${SANDBOX_SLUG}.dump.gz" \
            "$DUMP"
        ssh "root@${SANDBOX_HOST}" "rm -f /tmp/${SANDBOX_SLUG}.dump.gz"
        echo "  → snapshot saved to $DUMP ($(du -h $DUMP | cut -f1))"
    fi
fi

# ─── 5. Compose up ────────────────────────────────────────────────────
# Launch tier activates the `launch` compose profile, which brings up
# Supabase Studio alongside the base substrate. Sandbox provisioning
# (sandbox-orchestrator/provision.py) deliberately omits the profile so
# Studio doesn't ship on free tier — that's the density bet behind the
# £0.30/mo kept-sandbox cost.
echo "→ docker compose --profile launch up -d"
cd "$APP_DIR"
docker compose --profile launch pull || true   # pull any pre-built images; skip on first boot
docker compose --profile launch up -d
docker compose --profile launch ps

# ─── 6. Restore DB once postgres is healthy (if dump available) ────────
if [[ -n "${DUMP:-}" && -f "$DUMP" && ! -f "$APP_DIR/.bootstrap-db-restored" ]]; then
    echo "→ waiting for postgres container to accept connections..."
    for _ in $(seq 1 30); do
        if docker compose exec -T postgres pg_isready -U postgres >/dev/null 2>&1; then
            break
        fi
        sleep 2
    done
    echo "→ pg_restore from $DUMP"
    gunzip -c "$DUMP" | docker compose exec -T postgres \
        pg_restore -U postgres -d postgres --no-owner --no-privileges --clean --if-exists
    touch "$APP_DIR/.bootstrap-db-restored"
    echo "  → restore complete; sentinel written"
fi

# ─── 7. Final health check ─────────────────────────────────────────────
echo "→ health check via Caddy"
sleep 5  # let Caddy provision the TLS cert
if curl -sSL -o /dev/null -w '%{http_code}\n' "https://${DOMAIN}/" | grep -qE '^(200|301|302|3..)$'; then
    echo "✓ ${DOMAIN} serving"
else
    echo "⚠ ${DOMAIN} not responding yet — Caddy may still be provisioning TLS"
    echo "   Tail logs with: docker compose logs caddy"
fi

echo "── bootstrap_substrate.sh done $(date -u) ────────────────────"
