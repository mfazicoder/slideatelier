"""Centralised path resolution for per-user output isolation.

Every route that previously did `config.output_dir / "workflow" / job_id`
should call `resolve_job_dir(config, request, job_id)` instead. The resolver:

1. Looks up the deck in SQLite. If found and owner is a real user, returns
   that user's per-user path: <output_dir>/users/<owner>/workflow/<job_id>/.
2. Otherwise falls back to the legacy flat path. This covers anonymous (dev)
   sessions and pre-auth decks that are still owned by SYSTEM_USER_ID.

`new_job_dir(config, request, job_id)` is the variant for routes that are
*creating* a job: it auto-records ownership in SQLite (anonymous → no record;
authenticated → owner_user_id=user.id). Call it once at job creation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request

    from ..config import Config

from .db import SYSTEM_USER_ID, get_db
from .middleware import get_current_user, user_workflow_root


def resolve_job_dir(config: "Config", request: Optional["Request"], job_id: str) -> Path:
    """Pick the right on-disk dir for `job_id`. Backwards-compatible default.

    Resolution order:
    - If decks(job_id) has owner != SYSTEM, use that user's namespace.
    - Else if the authenticated user has the job under their namespace, use that.
    - Else fall back to the legacy flat layout (system / pre-auth).
    """
    db = get_db(config.output_dir)
    deck = db.get_deck(job_id)
    if deck is not None and deck.owner_user_id != SYSTEM_USER_ID:
        owner = db.get_user(deck.owner_user_id)
        if owner is not None:
            return user_workflow_root(config.output_dir, owner) / job_id

    user = get_current_user(request) if request is not None else None
    if user is not None and user.id != SYSTEM_USER_ID:
        candidate = user_workflow_root(config.output_dir, user) / job_id
        if candidate.exists():
            return candidate

    return config.output_dir / "workflow" / job_id


def new_job_dir(config: "Config", request: Optional["Request"], job_id: str) -> Path:
    """Path for a freshly-created job. Records ownership in SQLite."""
    db = get_db(config.output_dir)
    user = get_current_user(request) if request is not None else None
    owner_id = user.id if user is not None else SYSTEM_USER_ID
    db.record_deck(job_id, owner_id)
    if user is not None and user.id != SYSTEM_USER_ID:
        return user_workflow_root(config.output_dir, user) / job_id
    return config.output_dir / "workflow" / job_id


def workflow_root_for_request(config: "Config", request: Optional["Request"]) -> Path:
    """Where to enumerate workflows for the current viewer.

    Authenticated users see only their own; anonymous (dev) sees the legacy flat
    layout. Tests that don't sign in continue to hit the flat layout.
    """
    user = get_current_user(request) if request is not None else None
    return user_workflow_root(config.output_dir, user)
