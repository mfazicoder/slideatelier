"""Session middleware + helpers.

Reads the session cookie on every request and attaches `request.state.user`
(a User dataclass, or None for anonymous traffic). Routes that need auth call
`require_user(request)`; routes that should remain public ignore the state.

Also exposes:
- user_workflow_root(config, user) → Path
    Per-user `<output_dir>/users/<id>/workflow/` for authenticated users, or
    the legacy `<output_dir>/workflow/` for anonymous (dev) sessions.
- user_owns_job(config, user, job_id) → bool
    Looks up the SQLite decks table; SYSTEM_USER_ID is treated as owned by
    every authenticated user in dev mode (so the user's pre-auth deck still
    works after they sign up).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .db import SESSION_COOKIE_NAME, SYSTEM_USER_ID, User, get_db


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def user_workflow_root(output_dir: Path, user: Optional[User]) -> Path:
    """Where this user's workflow/<job_id>/ subtree lives.

    Anonymous (or system) → `<output_dir>/workflow/` — keeps backwards-compat
    with everything that pre-dated auth, including the user's existing DIFC
    deck at output/workflow/f0e1fdf77d19/.

    Authenticated → `<output_dir>/users/<user_id>/workflow/`.
    """
    if user is None or user.id == SYSTEM_USER_ID:
        return output_dir / "workflow"
    return output_dir / "users" / str(user.id) / "workflow"


def user_owns_job(output_dir: Path, user: Optional[User], job_id: str) -> bool:
    """Ownership check based on the SQLite decks table.

    Rules:
    - Anonymous (no user) — only allowed in dev mode, where the legacy flat
      layout is shared. Treat all jobs as accessible.
    - Authenticated user — they own a deck iff the decks row says so. They
      ALSO get access to legacy SYSTEM_USER_ID-owned decks in dev mode, so
      the user's pre-auth deck doesn't disappear after they sign up. In
      production we tighten this to strict ownership.
    """
    if user is None:
        return True  # dev mode / pre-auth — caller decides whether to proceed
    db = get_db(output_dir)
    deck = db.get_deck(job_id)
    if deck is None:
        # Not in the index — could be a freshly-created job that hasn't been
        # registered yet. The route that creates jobs should record_deck()
        # immediately; until then, fall back to filesystem inspection.
        return _legacy_dir_belongs_to_user(output_dir, user, job_id)
    if deck.owner_user_id == user.id:
        return True
    if deck.owner_user_id == SYSTEM_USER_ID and not _is_production():
        return True
    return False


def _legacy_dir_belongs_to_user(output_dir: Path, user: User, job_id: str) -> bool:
    """When the decks table has no row, infer ownership from filesystem.

    A job dir at output/users/<user.id>/workflow/<job_id>/ obviously belongs to
    `user`. A dir at output/workflow/<job_id>/ is treated as belonging to the
    system user — accessible to authenticated users in dev only.
    """
    own = output_dir / "users" / str(user.id) / "workflow" / job_id
    if own.exists():
        return True
    legacy = output_dir / "workflow" / job_id
    if legacy.exists() and not _is_production():
        return True
    return False


def _is_production() -> bool:
    return os.getenv("SLIDEATELIER_ENV", "").lower() == "production"


# ---------------------------------------------------------------------------
# FastAPI integration
# ---------------------------------------------------------------------------

class SessionMiddleware(BaseHTTPMiddleware):
    """Resolve the session cookie → User and attach to request.state."""

    async def dispatch(self, request: Request, call_next):
        token = request.cookies.get(SESSION_COOKIE_NAME, "")
        user: Optional[User] = None
        if token:
            try:
                # output_dir is read fresh from env so tests using
                # monkeypatch.setenv get the right DB.
                output_dir = Path(os.getenv("SLIDEATELIER_OUTPUT_DIR", "./output"))
                user = get_db(output_dir).get_user_for_session(token)
            except Exception:  # noqa: BLE001
                user = None
        request.state.user = user
        return await call_next(request)


def get_current_user(request: Request) -> Optional[User]:
    return getattr(request.state, "user", None)


def require_user(request: Request) -> User:
    """Dependency-style helper for routes that need an authenticated user.

    Raises a 401 with a Location-style header so HTML routes can redirect to
    /login. JSON-API consumers see a vanilla 401. The route handler itself
    decides which by checking request URL / Accept header.
    """
    user = get_current_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"Location": "/login"},
        )
    return user


def login_redirect_if_anonymous(request: Request) -> Optional[RedirectResponse]:
    """For HTML routes: returns a 302→/login response if the user is anonymous,
    else None (caller proceeds). Preserves the original URL in ?next=."""
    if get_current_user(request) is not None:
        return None
    target = "/login"
    nxt = request.url.path
    if nxt and nxt != "/" and nxt != "/login":
        from urllib.parse import quote
        target = f"/login?next={quote(nxt)}"
    return RedirectResponse(url=target, status_code=302)
