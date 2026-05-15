#!/usr/bin/env python3
"""
idle_suspend.py — 2-hour idle-suspend daemon for Hatchik sandbox tenants.

Companion to lifecycle.py. Where lifecycle.py handles the 7-day idle-archive
boundary (cold storage), THIS daemon handles the short-cycle 2-hour idle-
suspend (warm-stopped containers). Together they implement the £0.30/mo per
tenant unit economics: a sandbox costs us nothing while its containers are
stopped, and the lifecycle reconciler eventually reclaims the slot if the
customer never returns.

How it works
------------

Every ~2 minutes (driven by a systemd Timer, or the built-in --loop mode):

  1. Read the host-Caddy JSON access log to compute the most recent HTTP
     request timestamp per tenant subdomain.
  2. For each tenant in registry.json with status='live':
       - skip if per-tenant lifecycle.json sets {"idle_suspend": false}
       - skip if the tenant's container set is already stopped
       - otherwise: if (now - last_request) >= 2h, run
             docker compose -f /opt/hatchik-tenants/<slug>/docker-compose.yml stop
  3. Emit one structured-JSON line per tenant to /var/log/hatchik/idle-suspend.log
     and a single human-readable summary to journald via the systemd unit.

The daemon is idempotent. A tenant that's already stopped won't be stopped
again. A lock file (/var/lock/hatchik-idle-suspend.lock) ensures two
concurrent runs cannot collide — the second one exits cleanly.

Wake-on-request lives in a sibling shim, wake_on_request.py, which Caddy
proxies to as an error fallback when the tenant port is unreachable. See
runbooks/idle-suspend.md.

Usage
-----

    # Run once (used by systemd OnUnitActiveSec=2min Timer)
    python3 idle_suspend.py --once

    # Run continuously, sleeping 120s between ticks (foreground / debug)
    python3 idle_suspend.py --loop

    # Dry-run: log decisions, suspend nothing
    python3 idle_suspend.py --once --dry-run

    # Single tenant (testing)
    python3 idle_suspend.py --once --slug prepsheet --dry-run

    # JSON summary on stdout (handy for ad-hoc checks)
    python3 idle_suspend.py --once --dry-run --json

Env overrides (testing)
-----------------------

    HATCHIK_IDLE_SUSPEND_SECONDS=7200
        Idle threshold in seconds. Default 7200 (2 hours).

    HATCHIK_IDLE_SUSPEND_FAKE_NOW=2026-05-15T14:00:00Z
        Pretend "now" is this ISO instant. Lets tests rewind / fast-forward.

    HATCHIK_IDLE_SUSPEND_STATE_DIR
        Override TENANTS_DIR. The dry-run verification suite uses this to
        point at a synthetic /tmp tree.

    HATCHIK_IDLE_SUSPEND_LOG_DIR
        Override /var/log/hatchik.

    HATCHIK_IDLE_SUSPEND_LOCK_FILE
        Override /var/lock/hatchik-idle-suspend.lock.

    HATCHIK_IDLE_SUSPEND_CADDY_LOG
        Override /opt/hatchik-host-caddy/logs/access.log.

    HATCHIK_IDLE_SUSPEND_TICK_SECONDS=120
        --loop sleep between ticks. Default 120s.

Exit codes
----------
    0  clean run (suspended some, skipped some, all fine)
    1  fatal error (registry unreadable, lock acquired by another run, etc.)
    2  partial — at least one tenant errored but the run continued
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ─── Config ───────────────────────────────────────────────────────────────
DEFAULT_IDLE_SECONDS = 7200  # 2h
DEFAULT_TICK_SECONDS = 120   # poll cadence in --loop mode
DEFAULT_DOCKER_TIMEOUT = 60  # `docker compose stop` budget

TENANTS_DIR = Path(os.environ.get(
    "HATCHIK_IDLE_SUSPEND_STATE_DIR",
    os.environ.get("HATCHIK_TENANTS_DIR", "/opt/hatchik-tenants"),
))
REGISTRY_FILE = TENANTS_DIR / "registry.json"

HOST_CADDY_DIR = Path(os.environ.get("HATCHIK_HOST_CADDY_DIR", "/opt/hatchik-host-caddy"))
CADDY_ACCESS_LOG = Path(os.environ.get(
    "HATCHIK_IDLE_SUSPEND_CADDY_LOG",
    str(HOST_CADDY_DIR / "logs" / "access.log"),
))

LOG_DIR = Path(os.environ.get("HATCHIK_IDLE_SUSPEND_LOG_DIR", "/var/log/hatchik"))
EVENT_LOG = LOG_DIR / "idle-suspend.log"

LOCK_FILE = Path(os.environ.get(
    "HATCHIK_IDLE_SUSPEND_LOCK_FILE",
    "/var/lock/hatchik-idle-suspend.lock",
))

IDLE_SECONDS = int(os.environ.get("HATCHIK_IDLE_SUSPEND_SECONDS", str(DEFAULT_IDLE_SECONDS)))
TICK_SECONDS = int(os.environ.get("HATCHIK_IDLE_SUSPEND_TICK_SECONDS", str(DEFAULT_TICK_SECONDS)))

DOMAIN = "hatchik.com"


# ─── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("hatchik-idle-suspend")


def _open_event_log() -> Any:
    """Open the structured event log; degrade gracefully if /var/log/hatchik
    isn't writable (e.g., running as a non-root operator on a dev box)."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        return EVENT_LOG.open("a", buffering=1)  # line-buffered
    except OSError as e:
        log.warning("event-log %s unwritable (%s) — falling back to stderr-only", EVENT_LOG, e)
        return None


def emit_event(fh: Any, event: dict[str, Any]) -> None:
    """Emit one JSON line. Always also log a short human form via stdlib."""
    event = {"ts": now_utc().isoformat(), **event}
    line = json.dumps(event, sort_keys=True)
    if fh is not None:
        try:
            fh.write(line + "\n")
        except OSError as e:
            log.warning("event-log write failed: %s", e)
    # Human-readable trace (journald picks this up via the systemd unit)
    slug = event.get("slug", "-")
    action = event.get("action", "-")
    idle = event.get("idle_seconds")
    extra = f" idle={idle}s" if idle is not None else ""
    log.info("event slug=%s action=%s%s", slug, action, extra)


# ─── Time ─────────────────────────────────────────────────────────────────
def now_utc() -> datetime:
    fake = os.environ.get("HATCHIK_IDLE_SUSPEND_FAKE_NOW", "").strip()
    if fake:
        if fake.endswith("Z"):
            fake = fake[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(fake).astimezone(timezone.utc)
        except ValueError:
            log.warning("invalid HATCHIK_IDLE_SUSPEND_FAKE_NOW=%r — using wall clock", fake)
    return datetime.now(timezone.utc)


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


# ─── Registry + per-tenant lifecycle.json ────────────────────────────────
def load_registry() -> dict[str, Any]:
    if not REGISTRY_FILE.exists():
        log.warning("registry %s missing — nothing to reconcile", REGISTRY_FILE)
        return {"version": 1, "tenants": {}}
    try:
        return json.loads(REGISTRY_FILE.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.error("registry %s unreadable: %s", REGISTRY_FILE, e)
        return {"version": 1, "tenants": {}}


def read_tenant_lifecycle(slug: str) -> dict[str, Any]:
    """Per-tenant lifecycle.json — operator-editable opt-out flags.

    Schema:
        {
            "idle_suspend": false,   # opt out of 2h suspend (default: true)
            "note": "debugging X"    # free-text reason, surfaced in events
        }
    Missing file == defaults (suspend enabled).
    """
    f = TENANTS_DIR / slug / "lifecycle.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.warning("tenant %s: lifecycle.json unreadable (%s) — using defaults", slug, e)
        return {}


# ─── Caddy access-log parsing ────────────────────────────────────────────
def build_last_request_map(log_path: Path, slugs: set[str]) -> dict[str, datetime]:
    """Tail the Caddy JSON access log and return {slug -> most-recent ts}.

    Caddy v2 with `log` directive in JSON encoding writes one line per
    request, each containing a "ts" float (seconds since epoch) and a
    "request" object with a "host" field (e.g. "prepsheet.hatchik.com").

    We only consider requests whose host parses as <slug>.hatchik.com and
    whose slug is in the live-tenant set. Anything else (apex, status,
    docs, the unknown-subdomain 404) is ignored.

    The log file can be large; we stream the whole file once per tick. If
    the operator has logrotate configured (recommended — see runbook), the
    file stays bounded. Even at ~50 tenants × 1 req/min × 24h * 30 days,
    the file is ~2M lines / ~600 MB which is fine to stream once every
    2 min on a CAX31.
    """
    result: dict[str, datetime] = {}
    if not log_path.exists():
        log.warning("Caddy access log %s missing — every tenant looks idle", log_path)
        return result

    suffix = "." + DOMAIN
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                host = (rec.get("request") or {}).get("host") or ""
                # strip optional :port
                if ":" in host:
                    host = host.split(":", 1)[0]
                if not host.endswith(suffix):
                    continue
                slug = host[: -len(suffix)]
                if slug not in slugs:
                    continue
                ts_raw = rec.get("ts")
                try:
                    ts = datetime.fromtimestamp(float(ts_raw), timezone.utc)
                except (TypeError, ValueError):
                    continue
                prev = result.get(slug)
                if prev is None or ts > prev:
                    result[slug] = ts
    except OSError as e:
        log.error("failed to read Caddy access log %s: %s", log_path, e)
    return result


# ─── Docker container state ──────────────────────────────────────────────
def tenant_running(slug: str) -> bool | None:
    """Return True if any tenant container is in `running` state.

    Compose project name defaults to the directory name (<slug>), so the
    container names are <slug>-<service>-N. We list containers with that
    label / name prefix and check status.

    Returns None on `docker` failure — caller treats None as "skip; we
    can't tell".
    """
    # Test hook — lets the dry-run smoke test exercise the would-suspend
    # path without spinning up real containers. Honoured only when the
    # env var is set; production code never touches it.
    forced = os.environ.get("HATCHIK_IDLE_SUSPEND_FAKE_RUNNING", "").strip()
    if forced:
        # Allow per-slug overrides via "slug:bool,slug:bool" or a single
        # bool that applies to all slugs.
        if ":" in forced:
            for chunk in forced.split(","):
                k, _, v = chunk.partition(":")
                if k.strip() == slug:
                    return v.strip().lower() in ("1", "true", "yes")
            return None
        return forced.lower() in ("1", "true", "yes")
    try:
        r = subprocess.run(
            ["docker", "ps", "--filter", f"label=com.docker.compose.project={slug}",
             "--format", "{{.State}}"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("docker ps for %s failed: %s", slug, e)
        return None
    if r.returncode != 0:
        log.warning("docker ps for %s exited %s: %s", slug, r.returncode, r.stderr.strip()[:200])
        return None
    states = [s.strip() for s in r.stdout.splitlines() if s.strip()]
    return any(s == "running" for s in states)


def compose_stop(slug: str, *, dry_run: bool) -> bool:
    """Run `docker compose stop` for one tenant. Returns True on success.

    Uses `stop` (not `down`) — volumes + network state stay intact so a
    later `docker compose start` brings the tenant back identically. This
    is exactly the warm-suspend behaviour the wake shim relies on.
    """
    compose = TENANTS_DIR / slug / "docker-compose.yml"
    if not compose.exists():
        log.warning("tenant %s: %s missing — cannot suspend", slug, compose)
        return False
    if dry_run:
        log.info("[dry-run] would: docker compose -f %s stop", compose)
        return True
    try:
        r = subprocess.run(
            ["docker", "compose", "-f", str(compose), "stop"],
            cwd=str(compose.parent),
            capture_output=True, text=True, timeout=DOCKER_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.error("compose stop for %s errored: %s", slug, e)
        return False
    if r.returncode != 0:
        log.error("compose stop for %s exited %s: %s", slug, r.returncode, r.stderr.strip()[:300])
        return False
    return True


# ─── Reconcile one tenant ────────────────────────────────────────────────
def reconcile_tenant(
    slug: str,
    tenant: dict[str, Any],
    *,
    now: datetime,
    last_request: dict[str, datetime],
    dry_run: bool,
    event_fh: Any,
) -> dict[str, Any]:
    """Return a summary dict for this tenant."""
    entry: dict[str, Any] = {"slug": slug, "action": "no-action"}

    status = tenant.get("status")
    if status != "live":
        entry["action"] = f"skip-status-{status}"
        emit_event(event_fh, {"slug": slug, "action": entry["action"]})
        return entry

    # Per-tenant opt-out ── operator edits /opt/hatchik-tenants/<slug>/lifecycle.json
    lc = read_tenant_lifecycle(slug)
    if lc.get("idle_suspend") is False:
        entry["action"] = "skip-opt-out"
        entry["opt_out_note"] = lc.get("note") or ""
        emit_event(event_fh, {
            "slug": slug,
            "action": "skip-opt-out",
            "note": entry["opt_out_note"],
        })
        return entry

    # Note: brief explicitly says promoted tenants ALSO suspend after 2h
    # (they're paying for the slot, not for always-on). So unlike
    # lifecycle.py we do NOT skip on promoted_to here. We do record it in
    # the event log so audit trails show why a paying customer's sandbox
    # was warm-stopped.
    promoted_to = tenant.get("promoted_to")

    # Activity probe ── Caddy access log is the source of truth for "did
    # anyone hit this in the last 2h?". If we have no Caddy entry, fall
    # back to the registry's last_provisioned / created_at — better than
    # treating "no log line" as "infinitely active". The tenant may have
    # been provisioned <2h ago and nobody has visited yet; that's fine,
    # we'll wait. If it was provisioned >2h ago and there's still no log
    # line, it's safe to suspend.
    last = last_request.get(slug)
    fallback_used = False
    if last is None:
        for f in ("provisioned_at", "created_at", "live_at"):
            last = parse_iso(tenant.get(f))
            if last is not None:
                fallback_used = True
                entry["activity_fallback"] = f"registry.{f}"
                break
    if last is None:
        # Truly no signal. Be conservative — skip rather than suspend a
        # tenant we know nothing about.
        entry["action"] = "skip-no-activity-signal"
        emit_event(event_fh, {"slug": slug, "action": entry["action"]})
        return entry

    idle_seconds = int((now - last).total_seconds())
    entry["idle_seconds"] = idle_seconds
    entry["last_request"] = last.isoformat()
    if fallback_used:
        entry["last_request_source"] = entry["activity_fallback"]
    else:
        entry["last_request_source"] = "caddy-access-log"

    if idle_seconds < IDLE_SECONDS:
        entry["action"] = "skip-active"
        emit_event(event_fh, {
            "slug": slug,
            "action": "skip-active",
            "idle_seconds": idle_seconds,
            "threshold_seconds": IDLE_SECONDS,
        })
        return entry

    # Idempotency: if containers are already stopped, don't re-stop.
    running = tenant_running(slug)
    if running is False:
        entry["action"] = "skip-already-stopped"
        emit_event(event_fh, {
            "slug": slug,
            "action": "skip-already-stopped",
            "idle_seconds": idle_seconds,
        })
        return entry
    if running is None:
        entry["action"] = "skip-docker-probe-failed"
        emit_event(event_fh, {
            "slug": slug,
            "action": "skip-docker-probe-failed",
            "idle_seconds": idle_seconds,
        })
        return entry

    # Suspend.
    ok = compose_stop(slug, dry_run=dry_run)
    entry["action"] = "would-suspend" if dry_run else ("suspended" if ok else "suspend-failed")
    emit_event(event_fh, {
        "slug": slug,
        "action": entry["action"],
        "idle_seconds": idle_seconds,
        "threshold_seconds": IDLE_SECONDS,
        "promoted_to": promoted_to,
        "dry_run": dry_run,
    })
    return entry


# ─── Reconcile pass (one tick) ───────────────────────────────────────────
def reconcile_once(
    *,
    dry_run: bool,
    only_slug: str | None,
    event_fh: Any,
) -> dict[str, Any]:
    reg = load_registry()
    now = now_utc()
    tenants = reg.get("tenants", {})
    if only_slug:
        tenants = {k: v for k, v in tenants.items() if k == only_slug}

    slugs = {s for s, t in tenants.items() if t.get("status") == "live"}
    last_request = build_last_request_map(CADDY_ACCESS_LOG, slugs)

    log.info(
        "tick now=%s tenants=%d live=%d idle_threshold=%ss dry_run=%s",
        now.isoformat(), len(tenants), len(slugs), IDLE_SECONDS, dry_run,
    )

    summary: list[dict[str, Any]] = []
    errors = 0
    for slug, tenant in sorted(tenants.items()):
        try:
            entry = reconcile_tenant(
                slug, tenant,
                now=now, last_request=last_request,
                dry_run=dry_run, event_fh=event_fh,
            )
        except Exception as e:  # noqa: BLE001 — one tenant must not kill the run
            log.exception("tenant %s crashed during reconcile: %s", slug, e)
            entry = {"slug": slug, "action": "error", "error": str(e)}
            errors += 1
            emit_event(event_fh, {"slug": slug, "action": "error", "error": str(e)})
        summary.append(entry)

    return {
        "now": now.isoformat(),
        "dry_run": dry_run,
        "idle_threshold_seconds": IDLE_SECONDS,
        "tenants": summary,
        "errors": errors,
    }


# ─── Locking ─────────────────────────────────────────────────────────────
class _LockHeldElsewhere(RuntimeError):
    pass


def _acquire_lock(path: Path) -> Any:
    """Acquire an exclusive flock on `path`. Returns the open fd; caller
    must keep it alive for the duration of the run.

    Two concurrent runs is harmless in principle (the operations are
    idempotent), but it pollutes the event log and racing `docker compose
    stop`s waste CPU. So we make the second run exit cleanly.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning("lock dir %s unmakable: %s — proceeding without lock", path.parent, e)
        return None
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as e:
        log.warning("lock file %s unopenable: %s — proceeding without lock", path, e)
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        os.close(fd)
        if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            raise _LockHeldElsewhere(str(path)) from e
        raise
    # Write our pid for debugging — non-fatal if it fails.
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
    except OSError:
        pass
    return fd


def _release_lock(fd: Any) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except OSError:
        pass


# ─── Loop mode + signal handling ─────────────────────────────────────────
_STOP = False


def _install_signals() -> None:
    def _handler(signum: int, _frame: Any) -> None:
        global _STOP
        log.info("signal %s received — will exit after current tick", signum)
        _STOP = True

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def run_loop(*, dry_run: bool, only_slug: str | None, event_fh: Any) -> int:
    log.info("loop mode start — tick=%ds idle_threshold=%ds dry_run=%s",
             TICK_SECONDS, IDLE_SECONDS, dry_run)
    rc = 0
    while not _STOP:
        try:
            result = reconcile_once(dry_run=dry_run, only_slug=only_slug, event_fh=event_fh)
            if result["errors"]:
                rc = 2
        except Exception as e:  # noqa: BLE001
            log.exception("tick crashed: %s", e)
            rc = 2
        # Sleep in small slices so a SIGTERM doesn't wait the full tick.
        for _ in range(TICK_SECONDS):
            if _STOP:
                break
            time.sleep(1)
    log.info("loop mode exiting (rc=%d)", rc)
    return rc


# ─── Main ────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Hatchik 2-hour idle-suspend daemon")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one reconcile tick and exit (systemd-friendly)")
    mode.add_argument("--loop", action="store_true", help="Run continuously, sleeping HATCHIK_IDLE_SUSPEND_TICK_SECONDS between ticks")
    ap.add_argument("--dry-run", action="store_true", help="Log decisions, suspend nothing")
    ap.add_argument("--slug", help="Restrict to a single tenant slug (testing)")
    ap.add_argument("--json", action="store_true", help="(--once only) print summary JSON on stdout")
    args = ap.parse_args()

    if not (args.once or args.loop):
        args.once = True  # sensible default

    _install_signals()

    try:
        lock_fd = _acquire_lock(LOCK_FILE)
    except _LockHeldElsewhere as e:
        log.error("another idle-suspend run holds the lock (%s) — exiting", e)
        return 1

    event_fh = _open_event_log()
    try:
        if args.loop:
            return run_loop(dry_run=args.dry_run, only_slug=args.slug, event_fh=event_fh)
        result = reconcile_once(dry_run=args.dry_run, only_slug=args.slug, event_fh=event_fh)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 2 if result["errors"] else 0
    finally:
        if event_fh is not None:
            try:
                event_fh.close()
            except OSError:
                pass
        _release_lock(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
