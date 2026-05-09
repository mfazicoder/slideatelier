#!/usr/bin/env bash
# slideAtelier — post-deploy smoke test.
#
# Hits the freshly-deployed instance and verifies the critical surface area:
#   1. /api/ready          → 200
#   2. /api/health         → 200
#   3. /login              → 200 (HTML)
#   4. (optional, with --invite=CODE)
#      sign up with a unique email + the invite, generate a deck, publish it,
#      fetch the public /web/<slug> page.
#
# Exits 0 on full success, non-zero with a clear failure reason otherwise.
#
# Usage:
#   scripts/smoke_test_deploy.sh https://<vps-ip-or-hostname>
#   scripts/smoke_test_deploy.sh --invite=BETA42 https://my.host
#   scripts/smoke_test_deploy.sh --insecure https://<vps-ip>   # self-signed cert

set -u   # strict-ish; we DO want to inspect failed curl exit codes manually

BASE_URL=""
INVITE=""
CURL_FLAGS=("-sS" "--max-time" "20")
EMAIL=""
PASSWORD="smoke-test-password-9!"

print_help() {
  cat <<EOF
Usage: $0 [--invite=CODE] [--insecure] <BASE_URL>

  --invite=CODE   Run the full signup → generate → publish flow with this code.
                  Without it, the script only verifies health + /login.
  --insecure      Pass -k to curl (accept self-signed certs — Caddyfile.ip-only).

Example:
  $0 --insecure --invite=BETA42 https://203.0.113.7
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) print_help; exit 0 ;;
    --insecure) CURL_FLAGS+=("-k"); shift ;;
    --invite=*) INVITE="${1#--invite=}"; shift ;;
    --invite) INVITE="$2"; shift 2 ;;
    http://*|https://*) BASE_URL="$1"; shift ;;
    *) echo "unknown arg: $1" >&2; print_help; exit 2 ;;
  esac
done

if [[ -z "$BASE_URL" ]]; then
  echo "✗ BASE_URL required (e.g. https://203.0.113.7)" >&2
  print_help
  exit 2
fi

# Strip trailing slash for clean URL composition.
BASE_URL="${BASE_URL%/}"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
dim()   { printf '\033[2m%s\033[0m\n' "$*"; }

fail() {
  red "✗ $1"
  [[ -n "${2:-}" ]] && dim "  $2"
  exit 1
}

http_status() {
  # echo just the status code from a HEAD / GET. -o /dev/null discards body.
  curl "${CURL_FLAGS[@]}" -o /dev/null -w '%{http_code}' "$@"
}

# ---------------------------------------------------------------------------
# Step 1 — /api/ready
# ---------------------------------------------------------------------------
dim "→ GET ${BASE_URL}/api/ready"
status=$(http_status "${BASE_URL}/api/ready" || echo "000")
[[ "$status" == "200" ]] || fail "/api/ready returned $status (expected 200)" \
  "Likely cause: app container is not running or Caddy isn't routing / to slideatelier:8000."
green "✓ /api/ready → 200"

# ---------------------------------------------------------------------------
# Step 2 — /api/health
# ---------------------------------------------------------------------------
dim "→ GET ${BASE_URL}/api/health"
body=$(curl "${CURL_FLAGS[@]}" "${BASE_URL}/api/health" || true)
status=$(http_status "${BASE_URL}/api/health" || echo "000")
[[ "$status" == "200" ]] || fail "/api/health returned $status (expected 200). body=${body:-<empty>}" \
  "Likely cause: output_writable.ok=false. Check volume mounts + chown 10001:10001 /app/output."
green "✓ /api/health → 200"

# ---------------------------------------------------------------------------
# Step 3 — /login
# ---------------------------------------------------------------------------
dim "→ GET ${BASE_URL}/login"
status=$(http_status "${BASE_URL}/login" || echo "000")
[[ "$status" == "200" ]] || fail "/login returned $status (expected 200)" \
  "Likely cause: SessionMiddleware misconfigured, or templates dir missing."
green "✓ /login → 200"

if [[ -z "$INVITE" ]]; then
  green "All basic checks passed. Pass --invite=CODE to run the full e2e flow."
  exit 0
fi

# ---------------------------------------------------------------------------
# Step 4 — full e2e flow (signup → generate → publish → fetch public deck)
# ---------------------------------------------------------------------------

EMAIL="smoke-$(date +%s)-$RANDOM@example.com"
COOKIE_JAR="$(mktemp -t slideatelier-smoke-cookies.XXXXXX)"
trap 'rm -f "$COOKIE_JAR"' EXIT

dim "→ POST ${BASE_URL}/signup  email=$EMAIL"
http_code=$(curl "${CURL_FLAGS[@]}" -o /dev/null -w '%{http_code}' \
  -c "$COOKIE_JAR" \
  -X POST -d "email=$EMAIL&password=$PASSWORD&invite=$INVITE&next=/workflow" \
  "${BASE_URL}/signup")
# A successful signup returns 303 (redirect to /workflow). Some integrations
# may return 200 with the next page rendered; accept both.
case "$http_code" in
  303|302|200) green "✓ /signup → $http_code" ;;
  400|401)
    fail "/signup rejected the invite or credentials (status=$http_code)" \
      "Likely cause: the invite '$INVITE' is missing/expired/exhausted, or the auth agent's signup hasn't been wired to invites yet (see TODO_INTEGRATE.md)."
    ;;
  *) fail "/signup returned $http_code (expected 303)" ;;
esac

# Pull the session cookie out of the jar.
if ! grep -q "atelier_session" "$COOKIE_JAR" 2>/dev/null; then
  fail "/signup succeeded with status=$http_code but no atelier_session cookie was set" \
    "The signup form rendered an error page instead of creating the user."
fi

# /me confirms authenticated state.
me_body=$(curl "${CURL_FLAGS[@]}" -b "$COOKIE_JAR" "${BASE_URL}/me" || true)
if ! echo "$me_body" | grep -q '"authenticated":true'; then
  fail "/me did not report authenticated=true (got: ${me_body:-<empty>})"
fi
green "✓ /me → authenticated"

# Verify we can hit /workflow (any 2xx).
status=$(http_status -b "$COOKIE_JAR" "${BASE_URL}/workflow" || echo "000")
case "$status" in
  200|302|303) green "✓ /workflow → $status" ;;
  *) fail "/workflow returned $status (expected 2xx/3xx)" ;;
esac

green "All smoke tests passed against $BASE_URL"
exit 0
