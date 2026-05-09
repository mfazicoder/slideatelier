"""Production-grade health and readiness endpoints.

`/api/health` — liveness + dependency check (output dir writable, library
catalog loadable). Returns 503 if degraded so platforms (Fly, Render, Caddy)
can take the instance out of rotation.

`/api/ready` — lightweight readiness probe; just confirms the process is
alive and the FastAPI app is serving. Used for Fly's http_check polling.

The health checks deliberately do NOT touch the Anthropic API: an outage on
that side would force the user's running app to flap red, but the user wants
the app itself to stay reachable so they can read the dashboard. API key
billing/health is the user's concern, not ours.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..library import load_catalog
from ..metadata import PROMPT_VERSION


def _check_output_writable(output_dir: Path) -> tuple[bool, str | None]:
    """Verify we can create + delete a temp file in output_dir."""
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=str(output_dir), prefix=".healthcheck-", delete=True
        ):
            pass
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _check_catalog_loadable(catalog_path: Path) -> tuple[bool, str | None]:
    """Loadable means: file exists AND parses. A missing catalog is *not* a
    failure — the library scan is optional. Only treat parse errors as bad."""
    if not catalog_path.exists():
        return True, None  # Optional dependency; not present is fine.
    try:
        load_catalog(catalog_path)
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def register_health_routes(app: FastAPI, config_factory) -> None:
    """Mount /api/health (deep) and /api/ready (shallow). Replaces the basic
    /api/health that was inline in app.py — caller should remove that one.
    """

    @app.get("/api/health")
    def api_health() -> Any:
        config = config_factory()
        # Catalog path follows the same convention as app.py: relative to CWD.
        catalog_path = Path("./library/catalog.json")

        checks: dict[str, dict[str, Any]] = {}
        all_ok = True

        ok, err = _check_output_writable(config.output_dir)
        checks["output_writable"] = {"ok": ok, "path": str(config.output_dir)}
        if not ok:
            checks["output_writable"]["error"] = err
            all_ok = False

        ok, err = _check_catalog_loadable(catalog_path)
        checks["library_catalog"] = {
            "ok": ok,
            "path": str(catalog_path),
            "present": catalog_path.exists(),
        }
        if not ok:
            checks["library_catalog"]["error"] = err
            all_ok = False

        body = {
            "status": "ok" if all_ok else "degraded",
            "version": "0.4.0",
            "model": config.model,
            "prompt_version": PROMPT_VERSION,
            "api_key_set": bool(config.anthropic_api_key),
            "env": os.getenv("SLIDEATELIER_ENV", "development"),
            "checks": checks,
        }
        if not all_ok:
            return JSONResponse(status_code=503, content=body)
        return body

    @app.get("/api/ready")
    def api_ready() -> Any:
        """Cheapest possible probe — proves the event loop is alive."""
        return {"ready": True}
