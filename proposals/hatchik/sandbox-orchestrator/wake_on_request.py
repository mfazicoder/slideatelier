#!/usr/bin/env python3
"""
wake_on_request.py — HTTP shim that wakes suspended Hatchik tenant
sandboxes on first incoming request.

Architecture
------------

Caddy's per-tenant block (see host-caddy/tenant.caddy.template) tries
`reverse_proxy localhost:<TENANT_PORT>` first. When that fails (tenant is
warm-suspended → connection refused or 502), Caddy's `handle_errors`
directive proxies the request to THIS shim on localhost:18999. The
incoming request preserves the original Host header
(e.g. "prepsheet.hatchik.com"), which we use to identify which tenant
needs waking.

This shim:

  1. Parses <slug>.hatchik.com from the Host header.
  2. Looks up the tenant in registry.json.
  3. Coalesces concurrent wake requests for the same slug behind a single
     `docker compose start` (multiple impatient browser tabs == one wake).
  4. Runs `docker compose -f /opt/hatchik-tenants/<slug>/docker-compose.yml start`.
  5. Polls the tenant port until it responds (or a timeout fires).
  6. On success: returns a 200 splash with auto-refresh (5s) so the browser
     re-issues the request — by which time Caddy's primary reverse_proxy
     reaches a now-warm tenant and the user lands on the real page.
  7. On failure: returns a friendly 503 page with a manual retry button.

Why this pattern (vs a Caddy plugin)
------------------------------------

Caddy v2 has no built-in "run a shell command on request" directive, and
authoring a custom Caddy module means building+shipping a bespoke Caddy
binary. The host-Caddy here already has a custom Dockerfile.caddy-cf for
the cloudflare DNS module, but adding wake-on-request into Caddy itself
would couple two unrelated concerns and require a Go toolchain in the
build path.

A separate Python shim, proxied to via `handle_errors`, is:
  - stdlib-only (matches the no-new-deps constraint)
  - testable in isolation
  - independently restartable (suspend bug won't bring down Caddy)
  - easy to add per-tenant throttling / observability later

The companion Caddy snippet that activates this lives at
host-caddy/wake-shim.snippet — drop it into Caddyfile and provision.py
will already render compatible per-tenant routes.

Usage
-----

    # Foreground (systemd Type=simple)
    python3 wake_on_request.py --listen 127.0.0.1:18999

    # Dry-run: log requests, do not actually start anything
    python3 wake_on_request.py --listen 127.0.0.1:18999 --dry-run

Env overrides
-------------

    HATCHIK_TENANTS_DIR             default /opt/hatchik-tenants
    HATCHIK_WAKE_LOG_DIR            default /var/log/hatchik
    HATCHIK_WAKE_PORT_RANGE_START   default 18000  — where tenant ports start
    HATCHIK_WAKE_HEALTHCHECK_TIMEOUT default 30    — seconds to wait for tenant
    HATCHIK_WAKE_HEALTHCHECK_INTERVAL default 1    — seconds between polls
"""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DOMAIN = "hatchik.com"
TENANTS_DIR = Path(os.environ.get("HATCHIK_TENANTS_DIR", "/opt/hatchik-tenants"))
REGISTRY_FILE = TENANTS_DIR / "registry.json"
LOG_DIR = Path(os.environ.get("HATCHIK_WAKE_LOG_DIR", "/var/log/hatchik"))
EVENT_LOG = LOG_DIR / "wake-on-request.log"

HEALTHCHECK_TIMEOUT = int(os.environ.get("HATCHIK_WAKE_HEALTHCHECK_TIMEOUT", "30"))
HEALTHCHECK_INTERVAL = float(os.environ.get("HATCHIK_WAKE_HEALTHCHECK_INTERVAL", "1"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hatchik-wake")


# Global state — keyed by slug. Each entry is an Event we wait on.
_wake_lock = threading.Lock()
_wake_in_flight: dict[str, threading.Event] = {}
_wake_result: dict[str, bool] = {}

# Global dry-run flag set in main(); module-level so the request handler
# can read it without subclassing-with-state gymnastics.
DRY_RUN = False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit_event(event: dict[str, Any]) -> None:
    event = {"ts": now_iso(), **event}
    line = json.dumps(event, sort_keys=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with EVENT_LOG.open("a", buffering=1) as f:
            f.write(line + "\n")
    except OSError as e:
        log.warning("event-log write failed: %s", e)
    log.info("wake-event %s", line)


def load_registry() -> dict[str, Any]:
    if not REGISTRY_FILE.exists():
        return {"version": 1, "tenants": {}}
    try:
        return json.loads(REGISTRY_FILE.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.error("registry %s unreadable: %s", REGISTRY_FILE, e)
        return {"version": 1, "tenants": {}}


def slug_from_host(host: str) -> str | None:
    if ":" in host:
        host = host.split(":", 1)[0]
    suffix = "." + DOMAIN
    if not host.endswith(suffix):
        return None
    slug = host[: -len(suffix)]
    if not slug or "." in slug:
        return None
    return slug


def tenant_port(slug: str) -> int | None:
    reg = load_registry()
    t = reg.get("tenants", {}).get(slug)
    if not t:
        return None
    try:
        return int(t.get("port") or 0) or None
    except (TypeError, ValueError):
        return None


def port_reachable(port: int) -> bool:
    """TCP-connect probe. If the tenant's Caddy is up on its port, we
    consider it warm enough to release the waiter. Substrate inside the
    tenant may still be coming up but its own Caddy will buffer / 502 the
    same way the host one does — the customer sees one extra refresh at
    worst."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except (OSError, socket.timeout):
        return False


def wake_tenant(slug: str) -> bool:
    """Idempotent wake.

    Returns True if the tenant is reachable by the time we return.
    Coalesces concurrent callers for the same slug behind a single Event.
    """
    with _wake_lock:
        evt = _wake_in_flight.get(slug)
        if evt is not None:
            # Someone else is already waking this tenant — wait on their event.
            log.info("wake %s: piggy-backing on in-flight wake", slug)
            wait_evt = evt
            owner = False
        else:
            evt = threading.Event()
            _wake_in_flight[slug] = evt
            _wake_result.pop(slug, None)
            wait_evt = evt
            owner = True

    if not owner:
        wait_evt.wait(timeout=HEALTHCHECK_TIMEOUT + 5)
        return _wake_result.get(slug, False)

    # We're the wake owner.
    ok = False
    try:
        port = tenant_port(slug)
        if not port:
            emit_event({"slug": slug, "action": "wake-failed", "reason": "unknown-tenant"})
            return False

        # If it's already up, short-circuit. Happens if Caddy's error
        # handler fires on a transient hiccup but the tenant is actually
        # fine.
        if port_reachable(port):
            emit_event({"slug": slug, "action": "wake-noop", "reason": "already-up", "port": port})
            ok = True
            return True

        compose = TENANTS_DIR / slug / "docker-compose.yml"
        if not compose.exists():
            emit_event({"slug": slug, "action": "wake-failed", "reason": "compose-missing", "path": str(compose)})
            return False

        t0 = time.monotonic()
        if DRY_RUN:
            log.info("[dry-run] would: docker compose -f %s start", compose)
        else:
            try:
                r = subprocess.run(
                    ["docker", "compose", "-f", str(compose), "start"],
                    cwd=str(compose.parent),
                    capture_output=True, text=True, timeout=60,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                emit_event({"slug": slug, "action": "wake-failed", "reason": "compose-start-errored", "error": str(e)})
                return False
            if r.returncode != 0:
                emit_event({
                    "slug": slug, "action": "wake-failed",
                    "reason": "compose-start-nonzero", "rc": r.returncode,
                    "stderr": r.stderr.strip()[:300],
                })
                return False

        # Healthcheck poll. Brief says 10-15s target.
        deadline = t0 + HEALTHCHECK_TIMEOUT
        while time.monotonic() < deadline:
            if DRY_RUN:
                # In dry-run we have no actual container — just pretend
                # success after a token wait so the splash/log path is
                # exercised.
                time.sleep(0.1)
                ok = True
                break
            if port_reachable(port):
                ok = True
                break
            time.sleep(HEALTHCHECK_INTERVAL)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        emit_event({
            "slug": slug,
            "action": "wake-succeeded" if ok else "wake-failed",
            "reason": None if ok else "healthcheck-timeout",
            "port": port,
            "elapsed_ms": elapsed_ms,
            "dry_run": DRY_RUN,
        })
        return ok
    finally:
        with _wake_lock:
            _wake_result[slug] = ok
            evt.set()
            # Leave the entry in _wake_in_flight for ~5s so late piggy-
            # backers see the result, then a sweeper would drop it. For
            # simplicity we just drop immediately — late piggy-backers
            # will trigger a fresh wake which short-circuits via the
            # "already-up" path.
            _wake_in_flight.pop(slug, None)


# ─── HTTP layer ──────────────────────────────────────────────────────────
WARMING_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>Warming up — Hatchik</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
       background:#f6f5f1;color:#1a1a1a;margin:0;padding:0;
       display:flex;align-items:center;justify-content:center;min-height:100vh;}
  .card{background:#fff;border-radius:8px;padding:32px;max-width:480px;text-align:center;
        box-shadow:0 1px 3px rgba(0,0,0,0.05);}
  h1{font-size:20px;margin:0 0 12px;}
  p{font-size:15px;line-height:1.6;color:#555;margin:0 0 8px;}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#4f46e5;
       margin:0 4px;animation:pulse 1.2s ease-in-out infinite;}
  .dot:nth-child(2){animation-delay:0.2s;} .dot:nth-child(3){animation-delay:0.4s;}
  @keyframes pulse{0%,100%{opacity:.3}50%{opacity:1}}
</style></head><body>
<div class="card">
  <h1>Warming up your sandbox</h1>
  <p>Your sandbox was idle, so we paused it to save resources.</p>
  <p>It&rsquo;ll be ready in about 10&ndash;15 seconds.</p>
  <p style="margin-top:20px;"><span class="dot"></span><span class="dot"></span><span class="dot"></span></p>
</div></body></html>
"""

FAILED_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sandbox temporarily unavailable — Hatchik</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
       background:#f6f5f1;color:#1a1a1a;margin:0;padding:0;
       display:flex;align-items:center;justify-content:center;min-height:100vh;}
  .card{background:#fff;border-radius:8px;padding:32px;max-width:480px;text-align:center;
        box-shadow:0 1px 3px rgba(0,0,0,0.05);}
  h1{font-size:20px;margin:0 0 12px;}
  p{font-size:15px;line-height:1.6;color:#555;margin:0 0 16px;}
  a.btn{display:inline-block;background:#4f46e5;color:#fff;text-decoration:none;
        padding:10px 20px;border-radius:8px;font-weight:600;font-size:14px;}
</style></head><body>
<div class="card">
  <h1>Sandbox temporarily unavailable</h1>
  <p>We tried to wake your sandbox but it didn&rsquo;t come up in time. This is usually transient.</p>
  <p>Wait 30 seconds, then retry. If it persists, email
     <a href="mailto:hello@hatchik.com">hello@hatchik.com</a> with your subdomain.</p>
  <p><a class="btn" href="/">Try again</a></p>
</div></body></html>
"""


class WakeHandler(http.server.BaseHTTPRequestHandler):
    # Tighten the default log line — journald gets these.
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        log.info("%s - %s", self.address_string(), fmt % args)

    def _wake(self) -> None:
        host = self.headers.get("Host", "")
        slug = slug_from_host(host)
        if not slug:
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"unknown host\n")
            emit_event({"action": "request-rejected", "reason": "unparseable-host", "host": host})
            return

        emit_event({"slug": slug, "action": "request-received", "host": host, "path": self.path})
        ok = wake_tenant(slug)
        if ok:
            # 200 + meta-refresh. Returning 200 here means the browser
            # paints the splash; the meta-refresh sends the browser back
            # to the same URL 5s later, by which time the primary
            # reverse_proxy in Caddy reaches the warm tenant.
            body = WARMING_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # No caching — never let an intermediary keep this around.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        else:
            body = FAILED_HTML.encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # Hint the client to wait a bit before retrying.
            self.send_header("Retry-After", "30")
            self.end_headers()
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._wake()

    def do_POST(self) -> None:  # noqa: N802
        # Drain the body so the connection can be reused; we don't act on it.
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length:
                self.rfile.read(length)
        except (ValueError, OSError):
            pass
        self._wake()

    def do_HEAD(self) -> None:  # noqa: N802
        self._wake()


class _ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    global DRY_RUN
    ap = argparse.ArgumentParser(description="Hatchik wake-on-request shim")
    ap.add_argument("--listen", default="127.0.0.1:18999", help="host:port (default 127.0.0.1:18999)")
    ap.add_argument("--dry-run", action="store_true", help="Log wake requests but don't actually start containers")
    args = ap.parse_args()
    DRY_RUN = args.dry_run

    if ":" not in args.listen:
        log.error("invalid --listen %r (expected host:port)", args.listen)
        return 1
    host, port_s = args.listen.rsplit(":", 1)
    try:
        port = int(port_s)
    except ValueError:
        log.error("invalid port in --listen %r", args.listen)
        return 1

    server = _ThreadingServer((host, port), WakeHandler)
    log.info("wake-on-request listening on %s:%d (dry_run=%s)", host, port, DRY_RUN)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("SIGINT — shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
