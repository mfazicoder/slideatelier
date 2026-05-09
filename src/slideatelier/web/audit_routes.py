"""Sprint V — Deck Audit web routes.

Exposes:
    GET  /workflow/wireframe/<job_id>/audit
        Renders the audit page (issues grouped by slide, fix buttons).
    POST /workflow/wireframe/<job_id>/audit/fix-all
        Applies every auto-fixable issue and redirects back to the page.
    POST /workflow/wireframe/<job_id>/audit/fix/<issue_idx>
        Applies a single auto-fixable issue (by its index in the audit list).

The audit itself is a pure function (slideatelier.audit.audit_deck) — these
routes only persist deck.json mutations after auto-fix.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..audit import apply_audit_fixes, audit_deck, AUDIT_CODES
from ..config import Config
from ..models import SlideDeck
from ..template import load_default_template, load_template


def _job_dir(config: Config, job_id: str) -> Path:
    return config.output_dir / "workflow" / job_id


def _load_deck(job_dir: Path) -> SlideDeck:
    deck_path = job_dir / "deck.json"
    if not deck_path.exists():
        raise HTTPException(404, "deck.json not found for this workflow")
    try:
        return SlideDeck.model_validate_json(deck_path.read_text())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"deck.json invalid: {e}") from e


def _save_deck(job_dir: Path, deck: SlideDeck) -> None:
    """Save deck.json and snapshot history (so audit fixes integrate with undo)."""
    from .workflow_history import snapshot
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "deck.json").write_text(deck.model_dump_json(indent=2))
    snapshot(job_dir, "deck")


def _resolve_template(config: Config, job_dir: Path):
    name_path = job_dir / "template_name.txt"
    name = name_path.read_text().strip() if name_path.exists() else ""
    try:
        if name and name != "default":
            return load_template(config.templates_dir / f"{name}.json")
    except Exception:  # noqa: BLE001
        pass
    return load_default_template(config.templates_dir)


def register_audit_routes(app, templates, _config_callable):
    """Register Sprint V audit routes on the FastAPI app."""

    @app.get("/workflow/wireframe/{job_id}/audit", response_class=HTMLResponse)
    def page_audit(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        deck = _load_deck(job_dir)
        tpl = _resolve_template(config, job_dir)

        issues = audit_deck(deck, tpl)

        # Group by slide for the template.
        by_slide: dict[int, list[dict]] = {}
        for global_idx, issue in enumerate(issues):
            entry = issue.model_dump()
            entry["audit_idx"] = global_idx  # so per-issue fix buttons can address it
            by_slide.setdefault(issue.slide_idx, []).append(entry)

        # Counts for the header summary
        counts = {"error": 0, "warning": 0, "info": 0}
        for i in issues:
            counts[i.severity] = counts.get(i.severity, 0) + 1

        slide_groups = []
        for idx, slide in enumerate(deck.slides):
            slide_groups.append({
                "idx": idx,
                "title": slide.title,
                "layout": slide.layout,
                "issues": by_slide.get(idx, []),
            })

        return templates.TemplateResponse(
            request,
            "workflow/audit.html",
            {
                "job_id": job_id,
                "deck": deck,
                "issues": [i.model_dump() for i in issues],
                "slide_groups": slide_groups,
                "counts": counts,
                "audit_codes": AUDIT_CODES,
                "auto_fixable_count": sum(1 for i in issues if i.fix is not None),
            },
        )

    @app.post("/workflow/wireframe/{job_id}/audit/fix-all", response_class=HTMLResponse)
    def post_fix_all(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        deck = _load_deck(job_dir)
        tpl = _resolve_template(config, job_dir)

        issues = audit_deck(deck, tpl)
        new_deck, applied = apply_audit_fixes(deck, issues)
        if applied:
            _save_deck(job_dir, new_deck)

        # Redirect back to the audit page so the user sees fresh results.
        return RedirectResponse(
            url=f"/workflow/wireframe/{job_id}/audit",
            status_code=303,
        )

    @app.post(
        "/workflow/wireframe/{job_id}/audit/fix/{issue_idx}",
        response_class=HTMLResponse,
    )
    def post_fix_one(request: Request, job_id: str, issue_idx: int):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        deck = _load_deck(job_dir)
        tpl = _resolve_template(config, job_dir)

        issues = audit_deck(deck, tpl)
        if issue_idx < 0 or issue_idx >= len(issues):
            raise HTTPException(404, "issue index out of range")

        # Only apply this single issue.
        target = [issues[issue_idx]]
        new_deck, applied = apply_audit_fixes(
            deck, target, codes={issues[issue_idx].code}
        )
        if applied:
            _save_deck(job_dir, new_deck)

        return RedirectResponse(
            url=f"/workflow/wireframe/{job_id}/audit",
            status_code=303,
        )
