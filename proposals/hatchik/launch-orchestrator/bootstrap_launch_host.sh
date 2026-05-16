#!/usr/bin/env bash
#
# bootstrap_launch_host.sh — first-boot bootstrap for a freshly-provisioned
# **shared Launch host** (CAX41, ~25 tenants per box).
#
# Runs ON the new VPS, fired remotely by promote.py over SSH when the
# pool needs another host. By this point cloud-init has already installed
# docker.io / docker-compose-plugin / git / ufw / swap (see
# promote._cloud_init_script). This script does the host-level scaffolding
# that every tenant on the box will share:
#
#   - /opt/hatchik-tenants/         : per-tenant compose stacks
#   - /opt/hatchik-host-caddy/      : host Caddy compose + Caddyfile
#   - /opt/hatchik-host-caddy/tenants.d/<slug>.caddy : per-tenant routes
#
# It is INTENTIONALLY very similar to sandbox-orchestrator/setup-host.sh
# but the Caddyfile differs: each tenant route reverse-proxies to a
# *tenant Caddy* on an allocated host port (instead of the marketing
# site). promote.py drops per-tenant snippets into tenants.d/ from outside.
#
# Usage (on the new shared Launch host):
#   bootstrap_launch_host.sh
#
# Env (optional):
#   HATCHIK_HOST_LABEL  — informational tag for /etc/hatchik-host
#   CF_API_TOKEN        — Cloudflare API token for DNS-01 wildcard cert.
#                         Either set in the SSH environment when promote
#                         calls this script, or written into
#                         /opt/hatchik-host-caddy/.env afterwards.
#
# Idempotent. Re-running compares state and only re-does what changed.

set -euo pipefail

HOST_LABEL="${HATCHIK_HOST_LABEL:-launch-host-shared}"
TENANTS_DIR="/opt/hatchik-tenants"
HOST_CADDY_DIR="/opt/hatchik-host-caddy"
TENANTS_CADDY_D="${HOST_CADDY_DIR}/tenants.d"
LOG="/var/log/hatchik-launch-host-bootstrap.log"

exec > >(tee -a "$LOG") 2>&1

echo "── bootstrap_launch_host.sh started $(date -u) ─────────────────"
echo "HOST_LABEL=${HOST_LABEL}"

# ─── 1. Wait for docker daemon ─────────────────────────────────────────
echo "→ waiting for docker daemon..."
for _ in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then break; fi
    sleep 2
done
docker info >/dev/null

# ─── 2. Per-host directories ───────────────────────────────────────────
mkdir -p "${TENANTS_DIR}"
mkdir -p "${HOST_CADDY_DIR}"
mkdir -p "${TENANTS_CADDY_D}"
mkdir -p "${HOST_CADDY_DIR}/data"   # Caddy ACME state
mkdir -p "${HOST_CADDY_DIR}/config"

echo "${HOST_LABEL}" > /etc/hatchik-host

# ─── 3. Host Caddy: top-level Caddyfile + docker compose ───────────────
# The host Caddy terminates TLS for every tenant on this box and reverse-
# proxies to the tenant's allocated host port. Tenant snippets land in
# tenants.d/ from outside (promote.py rsyncs them in). We mount that dir
# read-only so a misbehaving tenant can't poison the host config.
if [[ ! -f "${HOST_CADDY_DIR}/Caddyfile" ]]; then
    cat > "${HOST_CADDY_DIR}/Caddyfile" <<'EOF'
# Host Caddy — Hatchik shared Launch host.
#
# Per-tenant routes are written to /etc/caddy/tenants.d/<slug>.caddy by
# promote.py (Phase 1 shared-host mode) and removed by
# decommission_launch.py. The import below pulls them in.
#
# To reload after promote.py writes a new tenant file:
#   docker exec hatchik-host-caddy-caddy-1 caddy reload \
#       --config /etc/caddy/Caddyfile

{
    email hello@hatchik.com
    # Uncomment for staging CA while testing (avoids rate limits):
    # acme_ca https://acme-staging-v02.api.letsencrypt.org/directory
}

# ─── Per-tenant routes — written by promote.py ──────────────────────
import tenants.d/*.caddy

# ─── Health endpoint on the host's IP (no TLS) ──────────────────────
# Useful for the orchestrator to verify the host's Caddy is alive
# without needing a domain. Bind to :80 only — leaves :443 for tenants.
:80 {
    respond /__hatchik_host_health "ok" 200
    respond /* "Hatchik Launch host — no tenant matched." 404
}
EOF
fi

if [[ ! -f "${HOST_CADDY_DIR}/docker-compose.yml" ]]; then
    cat > "${HOST_CADDY_DIR}/docker-compose.yml" <<'EOF'
# Host Caddy on the shared Launch box — TLS termination + per-tenant
# subdomain routing for every tenant compose stack on this host.
services:
  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./tenants.d:/etc/caddy/tenants.d:ro
      - ./data:/data
      - ./config:/config
    env_file:
      - .env
    network_mode: host
EOF
fi

if [[ ! -f "${HOST_CADDY_DIR}/.env" ]]; then
    # CF_API_TOKEN should be SSH-piped in by promote.py if available; else
    # the operator finishes the wire-up manually. We seed an empty value
    # so docker-compose doesn't fail to start the container.
    cat > "${HOST_CADDY_DIR}/.env" <<EOF
CF_API_TOKEN=${CF_API_TOKEN:-}
EOF
    chmod 600 "${HOST_CADDY_DIR}/.env"
fi

# ─── 4. Bring host Caddy up ────────────────────────────────────────────
cd "${HOST_CADDY_DIR}"
docker compose up -d
docker compose ps

# ─── 5. Open firewall — UFW was set by cloud-init but verify ───────────
ufw allow 80/tcp  || true
ufw allow 443/tcp || true
ufw status verbose | head -20 || true

# ─── 6. Health check the host Caddy ────────────────────────────────────
echo "→ host Caddy health check"
for _ in $(seq 1 15); do
    if curl -fsS http://127.0.0.1/__hatchik_host_health 2>/dev/null | grep -q "^ok$"; then
        echo "✓ host Caddy serving"
        break
    fi
    sleep 2
done

echo "── bootstrap_launch_host.sh done $(date -u) ────────────────────"
