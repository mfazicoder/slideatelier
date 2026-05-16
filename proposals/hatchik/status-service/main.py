"""
Hatchik status service — self-hosted uptime monitor.

Powers status.hatchik.com. Runs a background probe loop every 60s
against the public endpoints + the local sandbox host, persists
every check to SQLite, and serves a JSON snapshot for the static
status page.

Designed to live alongside hatchik-signup.service on the sandbox
host (port 8091, behind the host Caddy at status.hatchik.com).
SQLite is the only external dependency — no Prometheus, no Datadog,
no external SaaS.

Endpoints
─────────
GET  /api/status                 — current state of every component
                                   + 30-day uptime history + tenant
                                   fleet summary.
GET  /api/status/history.json    — rolling 30-day per-component daily
                                   uptime ratios (sparkline data).
POST /api/status/incident        — founder-only (X-Admin-Token) manual
                                   incident message.
GET  /healthz                    — service liveness.

The status page itself (status.html) is served as a static file by
the host Caddy; this service only powers /api/status*.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ─── Config ──────────────────────────────────────────────────────────────
DB_PATH = Path(os.environ.get("HATCHIK_STATUS_DB", "/var/lib/hatchik/status.db"))
REGISTRY_FILE = Path(
    os.environ.get(
        "HATCHIK_REGISTRY_FILE", "/opt/hatchik-tenants/registry.json"
    )
)
ADMIN_TOKEN = os.environ.get("HATCHIK_STATUS_ADMIN_TOKEN", "")
ALLOWED_ORIGINS = os.environ.get(
    "HATCHIK_STATUS_ALLOWED_ORIGINS",
    "https://status.hatchik.com,https://hatchik.com,https://www.hatchik.com",
).split(",")

# Probe targets — kept as constants so the test harness can patch them.
MARKETING_URL = os.environ.get("HATCHIK_PROBE_MARKETING", "https://hatchik.com/")
SIGNUP_HEALTH_URL = os.environ.get(
    "HATCHIK_PROBE_SIGNUP", "https://hatchik.com/api/healthz"
)

# Probe loop cadence and per-request budgets.
PROBE_INTERVAL_SECONDS = int(os.environ.get("HATCHIK_PROBE_INTERVAL", "60"))
HTTP_TIMEOUT_SECONDS = float(os.environ.get("HATCHIK_PROBE_TIMEOUT", "5"))
MARKETING_LATENCY_BUDGET_MS = 2000
SIGNUP_LATENCY_BUDGET_MS = 1000
TENANT_LATENCY_BUDGET_MS = 3000

# Host-level thresholds: yellow when crossed, red when far exceeded.
DISK_YELLOW_PCT = 80
DISK_RED_PCT = 92
MEM_FREE_YELLOW_MB = 1024
MEM_FREE_RED_MB = 256
LOAD_YELLOW = 4.0
LOAD_RED = 8.0

# Retain raw check rows for this many days. Daily uptime ratios for the
# sparkline are derived on the fly from this window.
HISTORY_RETENTION_DAYS = 31

# Components we probe. Order = display order in the JSON response.
COMPONENT_MARKETING = "marketing_site"
COMPONENT_SIGNUP = "signup_api"
COMPONENT_TENANT = "sandbox_provisioning"
COMPONENT_TLS = "host_tls"
COMPONENT_HOST = "sandbox_host"

COMPONENT_LABELS = {
    COMPONENT_MARKETING: "Marketing site",
    COMPONENT_SIGNUP: "Signup API",
    COMPONENT_TENANT: "Sandbox provisioning",
    COMPONENT_TLS: "Host TLS",
    COMPONENT_HOST: "Sandbox host",
}
COMPONENT_ORDER = [
    COMPONENT_MARKETING,
    COMPONENT_SIGNUP,
    COMPONENT_TENANT,
    COMPONENT_TLS,
    COMPONENT_HOST,
]

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("hatchik-status")


# ─── DB ──────────────────────────────────────────────────────────────────
def init_db() -> None:
    """Bootstrap the SQLite store. Idempotent + auto-creates parent dir.

    If the DB file is missing we create an empty schema so the service
    can start cold without manual intervention.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                component       TEXT NOT NULL,
                checked_at      TEXT NOT NULL,
                latency_ms      INTEGER,
                ok              INTEGER NOT NULL,
                status          TEXT NOT NULL,
                error           TEXT,
                detail          TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_checks_component_time "
            "ON checks(component, checked_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at      TEXT NOT NULL,
                title           TEXT NOT NULL,
                body            TEXT,
                severity        TEXT NOT NULL DEFAULT 'minor',
                resolved_at     TEXT
            )
            """
        )
        conn.commit()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def prune_old_checks() -> None:
    """Drop check rows older than HISTORY_RETENTION_DAYS."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=HISTORY_RETENTION_DAYS)
    ).isoformat()
    with db() as conn:
        conn.execute("DELETE FROM checks WHERE checked_at < ?", (cutoff,))
        conn.commit()


# ─── Probe helpers ───────────────────────────────────────────────────────
async def probe_http(
    client: httpx.AsyncClient,
    url: str,
    budget_ms: int,
    *,
    expect_status: int = 200,
) -> dict[str, Any]:
    """One HTTP GET, returning a probe-result dict.

    status is one of: "operational", "degraded", "down".
    """
    started = time.perf_counter()
    try:
        r = await client.get(url, follow_redirects=True)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if r.status_code != expect_status:
            return {
                "ok": False,
                "status": "down",
                "latency_ms": elapsed_ms,
                "error": f"HTTP {r.status_code}",
                "detail": None,
            }
        if elapsed_ms > budget_ms:
            return {
                "ok": True,
                "status": "degraded",
                "latency_ms": elapsed_ms,
                "error": f"slow ({elapsed_ms}ms > {budget_ms}ms budget)",
                "detail": None,
            }
        return {
            "ok": True,
            "status": "operational",
            "latency_ms": elapsed_ms,
            "error": None,
            "detail": None,
        }
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "status": "down",
            "latency_ms": elapsed_ms,
            "error": f"{type(e).__name__}: {e}"[:200],
            "detail": None,
        }
    except httpx.HTTPError as e:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "status": "down",
            "latency_ms": elapsed_ms,
            "error": f"{type(e).__name__}: {e}"[:200],
            "detail": None,
        }


def load_registry() -> dict[str, Any]:
    """Read the orchestrator registry. Returns empty when absent."""
    if not REGISTRY_FILE.exists():
        return {"version": 1, "tenants": {}}
    try:
        return json.loads(REGISTRY_FILE.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.warning("failed to read registry %s: %s", REGISTRY_FILE, e)
        return {"version": 1, "tenants": {}}


def tenant_fleet_summary() -> dict[str, int]:
    """Bucket tenants by status from the orchestrator registry."""
    reg = load_registry()
    counts = {"live": 0, "provisioning": 0, "failed": 0, "decommissioned": 0}
    for t in reg.get("tenants", {}).values():
        status = t.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    counts["total"] = sum(counts.values())
    return counts


def pick_live_tenant_url() -> str | None:
    """Pick one tenant URL we know to be 'live' for the proxy probe."""
    reg = load_registry()
    for t in reg.get("tenants", {}).values():
        if t.get("status") == "live" and t.get("url"):
            return str(t["url"])
    return None


async def probe_tenant(client: httpx.AsyncClient) -> dict[str, Any]:
    """Probe one live tenant's root URL to validate routing + TLS path.

    No live tenants → skipped (returns operational with detail="no tenants").
    """
    url = pick_live_tenant_url()
    if not url:
        return {
            "ok": True,
            "status": "operational",
            "latency_ms": None,
            "error": None,
            "detail": "no live tenants to probe",
            "probed_url": None,
        }
    result = await probe_http(client, url, TENANT_LATENCY_BUDGET_MS)
    result["probed_url"] = url
    return result


async def probe_tls(client: httpx.AsyncClient) -> dict[str, Any]:
    """Wildcard cert health — proven by HTTPS reaching hatchik.com.

    If the marketing GET succeeded, TLS is by definition working. We
    surface it as its own line so an outage caused by cert renewal
    looks distinct from app-level failures.
    """
    result = await probe_http(client, MARKETING_URL, MARKETING_LATENCY_BUDGET_MS)
    # Latency budget for TLS is generous — we only care that the
    # handshake succeeded. Any TLS-layer error httpx raises with would
    # have been caught by probe_http as ConnectError/HTTPError, so a
    # successful HTTP response means a fresh cert was served.
    if result["ok"]:
        return {
            "ok": True,
            "status": "operational",
            "latency_ms": result["latency_ms"],
            "error": None,
            "detail": "wildcard *.hatchik.com served",
        }
    return {
        "ok": False,
        "status": "down",
        "latency_ms": result["latency_ms"],
        "error": result["error"],
        "detail": None,
    }


def read_host_metrics() -> dict[str, Any]:
    """Disk free %, mem free MB, 1-minute load avg.

    Falls back gracefully on macOS-style envs where /proc isn't there
    (so local dev doesn't crash, even though the service only ships
    on Linux).
    """
    disk_used_pct: float | None = None
    disk_free_gb: float | None = None
    try:
        usage = shutil.disk_usage("/")
        disk_used_pct = (usage.used / usage.total) * 100
        disk_free_gb = usage.free / (1024**3)
    except OSError as e:
        log.warning("disk_usage failed: %s", e)

    mem_free_mb: float | None = None
    mem_total_mb: float | None = None
    try:
        with open("/proc/meminfo") as f:
            mem_lines = f.read().splitlines()
        info: dict[str, int] = {}
        for line in mem_lines:
            key, _, rest = line.partition(":")
            value = rest.strip().split()
            if value and value[0].isdigit():
                info[key] = int(value[0])  # kB
        if "MemAvailable" in info:
            mem_free_mb = info["MemAvailable"] / 1024
        elif "MemFree" in info:
            mem_free_mb = info["MemFree"] / 1024
        if "MemTotal" in info:
            mem_total_mb = info["MemTotal"] / 1024
    except (OSError, ValueError) as e:
        log.debug("meminfo unavailable: %s", e)

    load1: float | None = None
    try:
        with open("/proc/loadavg") as f:
            load1 = float(f.read().split()[0])
    except (OSError, ValueError, IndexError) as e:
        log.debug("loadavg unavailable: %s", e)

    return {
        "disk_used_pct": disk_used_pct,
        "disk_free_gb": disk_free_gb,
        "mem_free_mb": mem_free_mb,
        "mem_total_mb": mem_total_mb,
        "load1": load1,
    }


def probe_host() -> dict[str, Any]:
    """Synthesize a status from local host metrics.

    Red if any metric blows past the red threshold; yellow if any
    metric is above the yellow threshold; green otherwise.
    """
    metrics = read_host_metrics()
    status = "operational"
    reasons: list[str] = []

    disk_used = metrics.get("disk_used_pct")
    if disk_used is not None:
        if disk_used >= DISK_RED_PCT:
            status = "down"
            reasons.append(f"disk {disk_used:.0f}% full")
        elif disk_used >= DISK_YELLOW_PCT and status != "down":
            status = "degraded"
            reasons.append(f"disk {disk_used:.0f}% full")

    mem_free = metrics.get("mem_free_mb")
    if mem_free is not None:
        if mem_free <= MEM_FREE_RED_MB:
            status = "down"
            reasons.append(f"only {mem_free:.0f} MB free RAM")
        elif mem_free <= MEM_FREE_YELLOW_MB and status != "down":
            status = "degraded"
            reasons.append(f"only {mem_free:.0f} MB free RAM")

    load = metrics.get("load1")
    if load is not None:
        if load >= LOAD_RED:
            status = "down"
            reasons.append(f"load avg {load:.1f}")
        elif load >= LOAD_YELLOW and status != "down":
            status = "degraded"
            reasons.append(f"load avg {load:.1f}")

    return {
        "ok": status == "operational",
        "status": status,
        "latency_ms": None,
        "error": "; ".join(reasons) if reasons else None,
        "detail": None,
        "metrics": metrics,
    }


# ─── Snapshot + persistence ──────────────────────────────────────────────
def record_check(component: str, result: dict[str, Any]) -> None:
    """Append a probe result to the checks table."""
    with db() as conn:
        conn.execute(
            "INSERT INTO checks "
            "(component, checked_at, latency_ms, ok, status, error, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                component,
                datetime.now(timezone.utc).isoformat(),
                result.get("latency_ms"),
                1 if result.get("ok") else 0,
                result.get("status", "down"),
                result.get("error"),
                result.get("detail"),
            ),
        )
        conn.commit()


def overall_status(component_states: list[dict[str, Any]]) -> str:
    """Worst-of-all-components rollup."""
    statuses = {c["status"] for c in component_states}
    if "down" in statuses:
        return "major_outage"
    if "degraded" in statuses:
        return "partial_outage"
    return "operational"


def daily_uptime(component: str, days: int = 30) -> list[dict[str, Any]]:
    """Per-day uptime ratio for the last `days` days for a component.

    Returns oldest-first list of {date, ratio, samples}. ratio is 0..1
    (ok rows / total rows). Days with no samples surface with ratio=None.
    """
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    out: dict[str, dict[str, Any]] = {
        (start + timedelta(days=i)).isoformat(): {
            "date": (start + timedelta(days=i)).isoformat(),
            "ratio": None,
            "samples": 0,
        }
        for i in range(days)
    }
    with db() as conn:
        rows = conn.execute(
            "SELECT substr(checked_at, 1, 10) AS day, "
            "       SUM(ok) AS ok_count, COUNT(*) AS total "
            "FROM checks "
            "WHERE component = ? AND checked_at >= ? "
            "GROUP BY day",
            (component, start.isoformat()),
        ).fetchall()
    for row in rows:
        day = row["day"]
        if day in out and row["total"]:
            out[day]["ratio"] = row["ok_count"] / row["total"]
            out[day]["samples"] = row["total"]
    return [out[k] for k in sorted(out.keys())]


# In-memory cache populated by the probe loop. Reads of /api/status
# serve from here so the public endpoint never blocks on probes.
CACHE: dict[str, Any] = {
    "generated_at": None,
    "overall": "operational",
    "components": [],
    "fleet": {"live": 0, "provisioning": 0, "failed": 0,
              "decommissioned": 0, "total": 0},
    "incident": None,
    "uptime_30d": {},
}


def latest_check(component: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute(
            "SELECT checked_at, latency_ms, ok, status, error, detail "
            "FROM checks WHERE component = ? "
            "ORDER BY checked_at DESC LIMIT 1",
            (component,),
        ).fetchone()
    return dict(row) if row else None


def latest_incident() -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute(
            "SELECT id, created_at, title, body, severity, resolved_at "
            "FROM incidents "
            "WHERE resolved_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


async def run_probes_once() -> None:
    """One pass of every probe + persistence + cache refresh."""
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            marketing = await probe_http(
                client, MARKETING_URL, MARKETING_LATENCY_BUDGET_MS
            )
            signup = await probe_http(
                client, SIGNUP_HEALTH_URL, SIGNUP_LATENCY_BUDGET_MS
            )
            tenant = await probe_tenant(client)
            tls = await probe_tls(client)
        host = probe_host()

        record_check(COMPONENT_MARKETING, marketing)
        record_check(COMPONENT_SIGNUP, signup)
        record_check(COMPONENT_TENANT, tenant)
        record_check(COMPONENT_TLS, tls)
        record_check(COMPONENT_HOST, host)

        components = build_component_list(
            marketing=marketing,
            signup=signup,
            tenant=tenant,
            tls=tls,
            host=host,
        )
        fleet = tenant_fleet_summary()
        uptime = {c: daily_uptime(c) for c in COMPONENT_ORDER}

        CACHE.update(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "overall": overall_status(components),
                "components": components,
                "fleet": fleet,
                "incident": latest_incident(),
                "uptime_30d": uptime,
            }
        )
    except Exception as e:  # pragma: no cover - defensive
        log.exception("probe loop iteration failed: %s", e)


def build_component_list(
    *,
    marketing: dict[str, Any],
    signup: dict[str, Any],
    tenant: dict[str, Any],
    tls: dict[str, Any],
    host: dict[str, Any],
) -> list[dict[str, Any]]:
    """Shape the per-component blocks the UI consumes."""
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "key": COMPONENT_MARKETING,
            "label": COMPONENT_LABELS[COMPONENT_MARKETING],
            "status": marketing["status"],
            "latency_ms": marketing.get("latency_ms"),
            "error": marketing.get("error"),
            "detail": marketing.get("detail"),
            "checked_at": now,
        },
        {
            "key": COMPONENT_SIGNUP,
            "label": COMPONENT_LABELS[COMPONENT_SIGNUP],
            "status": signup["status"],
            "latency_ms": signup.get("latency_ms"),
            "error": signup.get("error"),
            "detail": signup.get("detail"),
            "checked_at": now,
        },
        {
            "key": COMPONENT_TENANT,
            "label": COMPONENT_LABELS[COMPONENT_TENANT],
            "status": tenant["status"],
            "latency_ms": tenant.get("latency_ms"),
            "error": tenant.get("error"),
            "detail": tenant.get("detail"),
            "probed_url": tenant.get("probed_url"),
            "checked_at": now,
        },
        {
            "key": COMPONENT_TLS,
            "label": COMPONENT_LABELS[COMPONENT_TLS],
            "status": tls["status"],
            "latency_ms": tls.get("latency_ms"),
            "error": tls.get("error"),
            "detail": tls.get("detail"),
            "checked_at": now,
        },
        {
            "key": COMPONENT_HOST,
            "label": COMPONENT_LABELS[COMPONENT_HOST],
            "status": host["status"],
            "latency_ms": host.get("latency_ms"),
            "error": host.get("error"),
            "detail": host.get("detail"),
            "metrics": host.get("metrics", {}),
            "checked_at": now,
        },
    ]


async def probe_loop() -> None:
    """Background task: probe forever, sleep between rounds, prune daily."""
    last_prune = 0.0
    while True:
        try:
            await run_probes_once()
            # Once a day, prune old rows.
            now = time.time()
            if now - last_prune > 86400:
                prune_old_checks()
                last_prune = now
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            log.exception("probe loop iteration crashed")
        await asyncio.sleep(PROBE_INTERVAL_SECONDS)


# ─── App ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    init_db()
    log.info("Hatchik status service started — DB at %s", DB_PATH)
    # Run one probe inline so /api/status returns useful data
    # immediately, then start the background loop.
    await run_probes_once()
    task = asyncio.create_task(probe_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Hatchik Status Service", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Admin-Token"],
)


class IncidentIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    body: str | None = Field(default=None, max_length=2000)
    severity: str = Field(default="minor")
    resolve: bool = False


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    """Public snapshot — serves from in-memory cache."""
    # Defensive: if the very first probe hasn't completed yet, surface
    # an empty-but-valid shape rather than nulls.
    if CACHE.get("generated_at") is None:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "overall": "operational",
            "components": [],
            "fleet": tenant_fleet_summary(),
            "incident": None,
            "uptime_30d": {},
        }
    return CACHE


@app.get("/api/status/history.json")
async def api_history() -> dict[str, Any]:
    """30-day daily uptime ratios per component, for sparklines."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "uptime_30d": {c: daily_uptime(c) for c in COMPONENT_ORDER},
    }


@app.post("/api/status/incident")
async def api_post_incident(
    incident: IncidentIn,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Founder-only: publish or resolve a manual incident message."""
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="admin API disabled")
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="bad admin token")
    if incident.severity not in {"minor", "major", "critical"}:
        raise HTTPException(status_code=422, detail="bad severity")

    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        if incident.resolve:
            conn.execute(
                "UPDATE incidents SET resolved_at = ? "
                "WHERE resolved_at IS NULL",
                (now,),
            )
            conn.commit()
            CACHE["incident"] = None
            return {"resolved": True, "at": now}
        cur = conn.execute(
            "INSERT INTO incidents (created_at, title, body, severity) "
            "VALUES (?, ?, ?, ?)",
            (now, incident.title, incident.body, incident.severity),
        )
        incident_id = cur.lastrowid
        conn.commit()
    CACHE["incident"] = {
        "id": incident_id,
        "created_at": now,
        "title": incident.title,
        "body": incident.body,
        "severity": incident.severity,
        "resolved_at": None,
    }
    return {"id": incident_id, "created_at": now}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
