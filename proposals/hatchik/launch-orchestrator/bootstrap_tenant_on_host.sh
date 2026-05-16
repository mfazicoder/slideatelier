#!/usr/bin/env bash
#
# bootstrap_tenant_on_host.sh — bring up a single tenant compose stack
# on a SHARED Launch host. Counterpart of bootstrap_substrate.sh (which
# is used in the dedicated-VPS path).
#
# Runs ON the shared host, fired remotely by promote.py over SSH. The
# host has already been prepared by bootstrap_launch_host.sh (host Caddy
# running, /opt/hatchik-tenants/ exists).
#
# Usage (on the shared Launch host):
#   bootstrap_tenant_on_host.sh \
#       --slug      launch-42 \
#       --repo-url  https://github.com/hatchik-tenants/foo.git \
#       --domain    foo.com \
#       --port-base 18100 \
#       [--sandbox-host 178.105.139.144 --sandbox-slug foo]
#
# Port allocation:
#   - port-base is the host port where the tenant Caddy will bind.
#   - The tenant's docker-compose maps its internal Caddy :80 to
#     127.0.0.1:${PORT_BASE} on the host. Host Caddy reverse-proxies
#     https://<domain> -> 127.0.0.1:${PORT_BASE}.
#   - No other tenant ports (postgres, supabase, etc.) are bound to the
#     host — they stay on the per-tenant bridge network. This keeps the
#     shared Postgres ports off the public network entirely.
#
# Idempotent. Re-running compares state and only re-does what changed.

set -euo pipefail

SLUG=""
REPO_URL=""
DOMAIN=""
PORT_BASE=""
SANDBOX_HOST=""
SANDBOX_SLUG=""
TENANTS_DIR="/opt/hatchik-tenants"
HOST_CADDY_TENANTS_D="/opt/hatchik-host-caddy/tenants.d"
LOG="/var/log/hatchik-launch-host-bootstrap.log"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --slug)           SLUG="$2";           shift 2 ;;
        --repo-url)       REPO_URL="$2";       shift 2 ;;
        --domain)         DOMAIN="$2";         shift 2 ;;
        --port-base)      PORT_BASE="$2";      shift 2 ;;
        --sandbox-host)   SANDBOX_HOST="$2";   shift 2 ;;
        --sandbox-slug)   SANDBOX_SLUG="$2";   shift 2 ;;
        --tenants-dir)    TENANTS_DIR="$2";    shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$SLUG" || -z "$REPO_URL" || -z "$DOMAIN" || -z "$PORT_BASE" ]]; then
    echo "Usage: $0 --slug SLUG --repo-url URL --domain DOMAIN --port-base PORT [--sandbox-host IP --sandbox-slug SLUG]" >&2
    exit 1
fi

exec > >(tee -a "$LOG") 2>&1

APP_DIR="${TENANTS_DIR}/${SLUG}"
echo "── bootstrap_tenant_on_host.sh ${SLUG} started $(date -u) ──────"
echo "SLUG=${SLUG}"
echo "REPO_URL=${REPO_URL}"
echo "DOMAIN=${DOMAIN}"
echo "PORT_BASE=${PORT_BASE}"
echo "APP_DIR=${APP_DIR}"
echo "SANDBOX_HOST=${SANDBOX_HOST:-(none)}"
echo "SANDBOX_SLUG=${SANDBOX_SLUG:-(none)}"

# ─── 1. Wait for docker daemon ─────────────────────────────────────────
for _ in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then break; fi
    sleep 2
done
docker info >/dev/null

mkdir -p "${TENANTS_DIR}"
mkdir -p "${HOST_CADDY_TENANTS_D}"

# ─── 2. Clone or update the customer's repo ────────────────────────────
if [[ -d "${APP_DIR}/.git" ]]; then
    echo "→ ${APP_DIR} exists; pulling latest"
    git -C "${APP_DIR}" fetch --depth 1 origin main
    git -C "${APP_DIR}" reset --hard origin/main
else
    echo "→ cloning ${REPO_URL} into ${APP_DIR}"
    git clone --depth 1 "${REPO_URL}" "${APP_DIR}"
fi

# ─── 3. Seed .env (idempotent — only if missing) ───────────────────────
if [[ ! -f "${APP_DIR}/.env" ]]; then
    if [[ -f "${APP_DIR}/.env.example" ]]; then
        cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
    else
        touch "${APP_DIR}/.env"
    fi
fi

# Always overwrite DOMAIN + the host-port mapping. The customer's compose
# is expected to reference ${HATCHIK_HOST_PORT_BASE} (or equivalent) when
# binding its Caddy. New substrate-template includes this — older repos
# fall back to the docker-compose override below.
upsert_env() {
    local key="$1" value="$2" file="$3"
    if grep -qE "^${key}=" "${file}"; then
        sed -i.bak -E "s|^${key}=.*$|${key}=${value}|" "${file}"
    else
        echo "${key}=${value}" >> "${file}"
    fi
    rm -f "${file}.bak"
}
upsert_env "DOMAIN" "${DOMAIN}" "${APP_DIR}/.env"
upsert_env "HATCHIK_HOST_PORT_BASE" "${PORT_BASE}" "${APP_DIR}/.env"
upsert_env "HATCHIK_HOST_MODE" "shared" "${APP_DIR}/.env"

# ─── 3b. Activate Caddyfile production block (same as bootstrap_substrate) ─
if [[ -f "${APP_DIR}/Caddyfile" ]] && grep -q '{{DOMAIN}}' "${APP_DIR}/Caddyfile"; then
    sed -i.bak "s|{{DOMAIN}}|${DOMAIN}|g" "${APP_DIR}/Caddyfile"
    awk -v dom="$DOMAIN" '
        BEGIN { in_block = 0 }
        $0 ~ "^# " dom " \\{" { in_block = 1 }
        in_block && /^# / { sub(/^# /, ""); print; if (/^\}/) in_block = 0; next }
        in_block && /^#$/  { print ""; next }
        { print }
    ' "${APP_DIR}/Caddyfile" > "${APP_DIR}/Caddyfile.tmp"
    mv "${APP_DIR}/Caddyfile.tmp" "${APP_DIR}/Caddyfile"
    rm -f "${APP_DIR}/Caddyfile.bak"
fi

# ─── 3c. Compose port-binding override (shared-host mode) ──────────────
# The customer's compose binds its tenant-Caddy to host port :8080 by
# default. On the shared host we rewrite that to bind to 127.0.0.1:PORT_BASE
# so only the host Caddy can reach it — no public exposure of the tenant
# Postgres / Supabase / app ports.
COMPOSE_FILE="${APP_DIR}/docker-compose.yml"
if [[ -f "${COMPOSE_FILE}" ]] && grep -q '"8080:80"' "${COMPOSE_FILE}"; then
    sed -i.bak "s|\"8080:80\"|\"127.0.0.1:${PORT_BASE}:80\"|" "${COMPOSE_FILE}"
    rm -f "${COMPOSE_FILE}.bak"
fi

# ─── 4. Migrate DB from sandbox (if applicable) ────────────────────────
if [[ -n "${SANDBOX_HOST}" && -n "${SANDBOX_SLUG}" ]]; then
    DUMP="/tmp/sandbox-${SANDBOX_SLUG}.dump.gz"
    if [[ -f "${APP_DIR}/.bootstrap-db-restored" ]]; then
        echo "→ DB already restored (sentinel exists); skipping"
    else
        echo "→ pulling DB snapshot from sandbox host"
        ssh -o StrictHostKeyChecking=accept-new \
            "root@${SANDBOX_HOST}" \
            "docker exec hatchik-${SANDBOX_SLUG}-postgres pg_dump -U postgres -Fc -Z 6 postgres > /tmp/${SANDBOX_SLUG}.dump.gz"
        rsync -az \
            -e "ssh -o StrictHostKeyChecking=accept-new" \
            "root@${SANDBOX_HOST}:/tmp/${SANDBOX_SLUG}.dump.gz" \
            "${DUMP}"
        ssh "root@${SANDBOX_HOST}" "rm -f /tmp/${SANDBOX_SLUG}.dump.gz"
    fi
fi

# ─── 5. Compose up ─────────────────────────────────────────────────────
# Each tenant gets its own docker network (default `<slug>_default`) so
# the per-tenant Postgres is isolated from sibling tenants on the same
# host. We pass -p <slug> so the project name is namespaced cleanly.
echo "→ docker compose -p ${SLUG} --profile launch up -d"
cd "${APP_DIR}"
docker compose -p "${SLUG}" --profile launch pull || true
docker compose -p "${SLUG}" --profile launch up -d
docker compose -p "${SLUG}" --profile launch ps

# ─── 6. Restore DB once postgres is healthy ────────────────────────────
if [[ -n "${DUMP:-}" && -f "${DUMP}" && ! -f "${APP_DIR}/.bootstrap-db-restored" ]]; then
    echo "→ waiting for postgres container to accept connections..."
    for _ in $(seq 1 30); do
        if docker compose -p "${SLUG}" exec -T postgres pg_isready -U postgres >/dev/null 2>&1; then
            break
        fi
        sleep 2
    done
    echo "→ pg_restore from ${DUMP}"
    gunzip -c "${DUMP}" | docker compose -p "${SLUG}" exec -T postgres \
        pg_restore -U postgres -d postgres --no-owner --no-privileges --clean --if-exists
    touch "${APP_DIR}/.bootstrap-db-restored"
fi

# ─── 7. Wait for tenant Caddy on PORT_BASE to be reachable ─────────────
echo "→ tenant health check on 127.0.0.1:${PORT_BASE}"
TENANT_OK=0
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "http://127.0.0.1:${PORT_BASE}/" 2>/dev/null; then
        TENANT_OK=1
        echo "✓ tenant responds on 127.0.0.1:${PORT_BASE}"
        break
    fi
    sleep 2
done
if [[ "${TENANT_OK}" -eq 0 ]]; then
    echo "⚠ tenant not responding on 127.0.0.1:${PORT_BASE} — check 'docker compose -p ${SLUG} logs'"
fi

# ─── 8. Write the host-Caddy snippet for this tenant ───────────────────
# promote.py also writes this from outside, but doing it here too means
# bootstrap_tenant_on_host.sh produces a working tenant if invoked
# directly (operator recovery).
SNIPPET="${HOST_CADDY_TENANTS_D}/${SLUG}.caddy"
if [[ ! -f "${SNIPPET}" ]]; then
    cat > "${SNIPPET}" <<EOF
# Auto-generated by bootstrap_tenant_on_host.sh — tenant ${SLUG}
${DOMAIN} {
    tls {
        dns cloudflare {env.CF_API_TOKEN}
    }
    encode gzip zstd
    reverse_proxy 127.0.0.1:${PORT_BASE} {
        header_up X-Forwarded-Host {host}
        header_up X-Forwarded-Proto {scheme}
    }
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Frame-Options "DENY"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
    }
}
EOF
    # Best-effort reload — non-fatal because promote.py reloads after
    # writing the canonical snippet from outside too.
    docker exec hatchik-host-caddy-caddy-1 caddy reload \
        --config /etc/caddy/Caddyfile 2>/dev/null || true
fi

echo "── bootstrap_tenant_on_host.sh ${SLUG} done $(date -u) ─────────"
