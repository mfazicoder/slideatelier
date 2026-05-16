#!/usr/bin/env bash
# deploy_docs.sh — rsync the customer-facing docs site to the
# sandbox host's /var/www/hatchik-docs/.
#
# Customer-facing promise: docs.hatchik.com serves the eight-page
# documentation site. The Caddyfile already routes
#   docs.hatchik.com { root * /var/www/hatchik-docs }
# (see sandbox-orchestrator/host-caddy/Caddyfile, "Docs" block).
#
# This script syncs the source-of-truth HTML files from the orchestrator
# repo's proposals/hatchik/docs/ into that webroot, sets ownership +
# permissions, and reloads Caddy so the changes are live.
#
# Usage (run from the orchestrator workstation OR via CI):
#   ./deploy_docs.sh                    # default host = $HATCHIK_SANDBOX_HOST
#   ./deploy_docs.sh user@1.2.3.4       # explicit target
#
# Idempotent. Safe to run on every merge to main.
set -euo pipefail

TARGET="${1:-${HATCHIK_SANDBOX_HOST:-root@sandbox.hatchik.com}}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../docs" && pwd)"
WEBROOT="/var/www/hatchik-docs"
CADDY_CONTAINER="${HATCHIK_HOST_CADDY_CONTAINER:-hatchik-host-caddy-caddy-1}"

echo "→ Deploying docs from $SOURCE_DIR to $TARGET:$WEBROOT"

ssh "$TARGET" "mkdir -p $WEBROOT && chown root:root $WEBROOT && chmod 755 $WEBROOT"

# Sync the docs dir contents (HTML + any assets) but skip backup snapshots.
rsync -av --delete \
  --exclude='backup-*' \
  --exclude='.DS_Store' \
  "$SOURCE_DIR/" "$TARGET:$WEBROOT/"

ssh "$TARGET" "find $WEBROOT -type f -name '*.html' -exec chmod 644 {} \\;"

# Reload Caddy so any config tweaks land — also bumps file cache.
echo "→ Reloading Caddy on $TARGET"
ssh "$TARGET" "docker exec $CADDY_CONTAINER caddy reload --config /etc/caddy/Caddyfile || \
               docker restart $CADDY_CONTAINER"

echo "✓ docs.hatchik.com updated"
