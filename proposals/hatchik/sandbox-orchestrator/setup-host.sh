#!/usr/bin/env bash
#
# setup-host.sh — one-time bootstrap on the Hatchik sandbox host.
#
# Run as root after the substrate-template smoke test confirms the host can
# run the substrate. This wires up:
#   - /opt/hatchik-tenants/        (per-tenant compose stack directories)
#   - /opt/hatchik-host-caddy/     (host-level Caddy with Cloudflare DNS)
#   - /opt/hatchik-orchestrator/   (provision.py, decommission.py)
#   - Host Caddy systemd unit running on 80/443
#   - Tenant Caddy can no longer claim 80/443 — host Caddy owns them
#
# Idempotent — safe to re-run.

set -euo pipefail

CF_API_TOKEN="${CF_API_TOKEN:-}"
if [ -z "$CF_API_TOKEN" ]; then
    echo "ERROR: CF_API_TOKEN env var must be set (Cloudflare token with Zone:DNS:Edit for hatchik.com)"
    exit 1
fi

echo "── 1. directories ──"
mkdir -p /opt/hatchik-tenants
mkdir -p /opt/hatchik-host-caddy/tenants.d
mkdir -p /opt/hatchik-orchestrator

echo "── 2. registry ──"
if [ ! -f /opt/hatchik-tenants/registry.json ]; then
    echo '{"version": 1, "tenants": {}}' > /opt/hatchik-tenants/registry.json
fi

echo "── 3. install httpx for provision.py (host Python) ──"
apt-get install -y -qq python3-httpx 2>/dev/null \
    || pip3 install --break-system-packages httpx >/dev/null

echo "── 4. host Caddy: build image + bring up ──"
cd /opt/hatchik-host-caddy
# Files (Caddyfile, docker-compose.yml, Dockerfile.caddy-cf) must already be
# rsync'd into this directory before running this script.
if ! grep -q "CF_API_TOKEN" /opt/hatchik-host-caddy/.env 2>/dev/null; then
    echo "CF_API_TOKEN=$CF_API_TOKEN" > /opt/hatchik-host-caddy/.env
    chmod 600 /opt/hatchik-host-caddy/.env
fi
docker compose --project-name hatchik-host-caddy build
docker compose --project-name hatchik-host-caddy up -d

echo "── 5. open port 8080 in UFW (drop later — only needed for substrate smoke test) ──"
ufw status | grep -q 8080 || ufw allow 8080/tcp comment 'tenant caddy smoke test' >/dev/null

echo "── 6. wait for host Caddy ──"
sleep 5
docker compose --project-name hatchik-host-caddy ps

echo
echo "═════════ BOOTSTRAP DONE ═════════"
echo "Host Caddy serving 80/443. Provisioning script ready at:"
echo "  /opt/hatchik-orchestrator/provision.py <signup_id>"
echo ""
echo "Next: test by provisioning a demo tenant:"
echo "  cd /opt/hatchik-orchestrator"
echo "  ./provision.py --slug demo --email you@example.com --product 'Hatchik Demo'"
