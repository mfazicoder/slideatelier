"""Sprint Q — analytics ingest + dashboard routes.

Endpoints:
  POST /api/events                          - public ingest from beacon JS
  GET  /workflow/<job_id>/analytics         - dashboard for the deck owner
  POST /workflow/<job_id>/analytics/toggle  - flip analytics_enabled on/off

The ingest endpoint deliberately accepts anonymous traffic — that's the whole
point of a viewer beacon — but applies aggressive validation in `analytics.
validate_payload`. We never log anything that could re-identify the visitor.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import analytics
from ..config import Config


def _job_dir(config: Config, job_id: str) -> Path:
    if not re.match(r"^[A-Za-z0-9_-]+$", job_id or ""):
        raise HTTPException(400, "invalid job_id")
    return config.output_dir / "workflow" / job_id


def register_analytics_routes(app, templates, _config_callable):
    """Wire ingest, dashboard, and opt-out endpoints."""

    # ---- Ingest -------------------------------------------------------------

    @app.post("/api/events")
    async def api_events(request: Request):
        config = _config_callable()
        # Read raw bytes so we can enforce the 2KB cap *before* JSON parsing,
        # avoiding DoS via massive blobs.
        body = await request.body()
        if len(body) > analytics.MAX_PAYLOAD_BYTES:
            raise HTTPException(400, "payload too large")
        try:
            payload = analytics.validate_payload(body)
        except analytics.AnalyticsValidationError:
            # Don't echo the detailed reason — could help an attacker probe
            # which fields they're getting tripped up by.
            raise HTTPException(400, "bad payload")

        # Belt-and-braces: only persist events for slugs we actually published.
        # Skip the check if the legacy slug index isn't around (e.g. fresh DB
        # under tests) — record_event itself doesn't depend on it.
        slug_index_path = config.output_dir / "web_slugs.json"
        if slug_index_path.exists():
            try:
                import json
                idx = json.loads(slug_index_path.read_text())
                if isinstance(idx, dict) and payload["slug"] in idx:
                    if idx[payload["slug"]] != payload["deck_id"]:
                        # deck_id mismatch — possible spoof. Drop.
                        raise HTTPException(400, "bad payload")
            except (OSError, ValueError):
                pass

        # Per-deck opt-out: if owner has analytics disabled, silently drop.
        # Returning 204 keeps the beacon silent if the page was cached before
        # the toggle flipped.
        try:
            settings = analytics.load_settings(_job_dir(config, payload["deck_id"]))
            if not settings.get("analytics_enabled", True):
                return JSONResponse({"ok": True, "stored": False}, status_code=204)
        except HTTPException:
            # Bad job_id — still bail (don't write to DB).
            raise HTTPException(400, "bad payload")

        analytics.record_event(payload, output_dir=config.output_dir)
        return JSONResponse({"ok": True, "stored": True})

    # ---- Dashboard ----------------------------------------------------------

    @app.get("/workflow/{job_id}/analytics", response_class=HTMLResponse)
    def page_analytics(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        settings = analytics.load_settings(job_dir)
        funnel = analytics.deck_funnel(job_id, output_dir=config.output_dir)
        dwell = analytics.slide_dwell(job_id, output_dir=config.output_dir)
        ctas = analytics.cta_ctr(job_id, output_dir=config.output_dir)
        sources = analytics.top_referrers(
            job_id, limit=10, output_dir=config.output_dir
        )
        sessions = analytics.total_sessions(job_id, output_dir=config.output_dir)
        # Look up the public URL if the deck is published.
        public_url = ""
        slug_path = job_dir / "web_slug.txt"
        if slug_path.exists():
            slug = slug_path.read_text().strip()
            if slug:
                public_url = f"/web/{slug}"
        return templates.TemplateResponse(
            request,
            "workflow/analytics.html",
            {
                "job_id": job_id,
                "settings": settings,
                "funnel": funnel,
                "dwell": dwell,
                "ctas": ctas,
                "sources": sources,
                "total_sessions": sessions,
                "public_url": public_url,
            },
        )

    # ---- Opt-out toggle -----------------------------------------------------

    @app.post("/workflow/{job_id}/analytics/toggle")
    async def toggle_analytics(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        # Accept either a JSON body {"analytics_enabled": bool} or a form.
        enabled = True
        try:
            data = await request.json()
            enabled = bool(data.get("analytics_enabled", True))
        except Exception:  # noqa: BLE001
            try:
                form = await request.form()
                enabled = form.get("analytics_enabled") in ("on", "true", "1", "yes")
            except Exception:  # noqa: BLE001
                pass
        merged = analytics.save_settings(job_dir, {"analytics_enabled": enabled})
        if request.headers.get("HX-Request"):
            # Just bounce back to the analytics page so the toggle UI updates.
            return RedirectResponse(
                url=f"/workflow/{job_id}/analytics", status_code=303
            )
        return JSONResponse({"analytics_enabled": merged["analytics_enabled"]})
