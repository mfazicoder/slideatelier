"""Email + password auth routes — /signup, /login, /logout.

Sessions live in a HttpOnly Secure-when-prod SameSite=Lax cookie with a 30-day
TTL. Rate limit: max 5 login attempts per IP per 15 minutes (in-memory token
bucket; resets when the process restarts — fine for a single-worker app).
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .db import SESSION_COOKIE_NAME, SESSION_TTL_DAYS, get_db
from .passwords import (
    hash_password,
    validate_password_strength,
    verify_password,
)

# RFC 5322-ish, deliberately permissive. We only care that it has a single @.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_RATE_WINDOW_SEC = 15 * 60
_RATE_MAX_ATTEMPTS = 5

_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_login_attempts_lock = threading.Lock()


def _is_production() -> bool:
    return os.getenv("SLIDEATELIER_ENV", "").lower() == "production"


def _client_ip(request: Request) -> str:
    """Best-effort. Honour X-Forwarded-For first hop when present (we sit
    behind a single trusted reverse proxy in prod)."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    cutoff = now - _RATE_WINDOW_SEC
    with _login_attempts_lock:
        bucket = _login_attempts[ip]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        return len(bucket) >= _RATE_MAX_ATTEMPTS


def _record_failed_attempt(ip: str) -> None:
    now = time.monotonic()
    with _login_attempts_lock:
        _login_attempts[ip].append(now)


def _clear_attempts(ip: str) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(ip, None)


def reset_rate_limits() -> None:
    """Test helper — wipe the in-memory bucket so each test starts clean."""
    with _login_attempts_lock:
        _login_attempts.clear()


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=_is_production(),
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def _output_dir_path() -> Path:
    return Path(os.getenv("SLIDEATELIER_OUTPUT_DIR", "./output"))


def _safe_next(value: Optional[str]) -> str:
    """Sanitise the ?next= param so we don't get used as an open redirect."""
    if not value:
        return "/workflow"
    if not value.startswith("/") or value.startswith("//"):
        return "/workflow"
    return value


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_auth_routes(app, templates: Jinja2Templates) -> None:
    @app.get("/signup", response_class=HTMLResponse)
    def page_signup(request: Request, next: str | None = None):
        if getattr(request.state, "user", None) is not None:
            return RedirectResponse(url=_safe_next(next), status_code=302)
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": None, "email": "", "next": _safe_next(next)},
        )

    @app.post("/signup")
    def post_signup(
        request: Request,
        email: str = Form(""),
        password: str = Form(""),
        invite: str = Form(""),
        next: str = Form("/workflow"),
    ):
        email = (email or "").strip().lower()
        invite = (invite or "").strip()
        next_url = _safe_next(next)
        ctx_email = email

        def _err(msg: str, status: int = 400) -> HTMLResponse:
            return templates.TemplateResponse(
                request,
                "auth/signup.html",
                {"error": msg, "email": ctx_email, "next": next_url},
                status_code=status,
            )

        if not email or not _EMAIL_RE.match(email):
            return _err("Enter a valid email address.")
        pw_err = validate_password_strength(password)
        if pw_err:
            return _err(pw_err)

        # Sprint Z.invite: private-beta invite gate. Validate before user creation
        # so a partial signup doesn't burn the code; consume after success.
        # The gate is on by default in production; dev/test sets
        # SLIDEATELIER_INVITE_GATE=off to bypass so existing tests stay green.
        import os as _os
        gate = _os.getenv("SLIDEATELIER_INVITE_GATE", "").lower()
        env = _os.getenv("SLIDEATELIER_ENV", "development").lower()
        gate_active = gate == "on" or (gate != "off" and env == "production")
        from . import invites as _invites
        if gate_active:
            try:
                _invites.validate_invite(invite)
            except _invites.InviteNotFound:
                return _err("Invite code is required and must be valid. Email founder@ to request one.")
            except _invites.InviteExpired:
                return _err("That invite code has expired. Request a fresh one.")
            except _invites.InviteExhausted:
                return _err("That invite code has been used up. Request a fresh one.")
            except _invites.InviteError as e:
                return _err(str(e))

        db = get_db(_output_dir_path())
        if db.get_user_by_email(email) is not None:
            return _err("An account with that email already exists. Try logging in.")
        try:
            user = db.create_user(email, hash_password(password))
        except Exception as e:  # noqa: BLE001
            return _err(f"Could not create account: {type(e).__name__}", status=500)

        # Burn the invite use only after the user record is committed.
        if gate_active:
            try:
                _invites.consume_invite(invite)
            except Exception:  # noqa: BLE001 — user already created; log and continue
                import logging
                logging.getLogger(__name__).warning("invite_consume_failed", extra={"code": invite[:4] + "…"})

        token = db.create_session(user.id)
        response = RedirectResponse(url=next_url, status_code=303)
        _set_session_cookie(response, token)
        return response

    @app.get("/login", response_class=HTMLResponse)
    def page_login(request: Request, next: str | None = None):
        if getattr(request.state, "user", None) is not None:
            return RedirectResponse(url=_safe_next(next), status_code=302)
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": None, "email": "", "next": _safe_next(next)},
        )

    @app.post("/login")
    def post_login(
        request: Request,
        email: str = Form(""),
        password: str = Form(""),
        next: str = Form("/workflow"),
    ):
        email = (email or "").strip().lower()
        next_url = _safe_next(next)
        ip = _client_ip(request)
        ctx_email = email

        def _err(msg: str, status: int = 400) -> HTMLResponse:
            return templates.TemplateResponse(
                request,
                "auth/login.html",
                {"error": msg, "email": ctx_email, "next": next_url},
                status_code=status,
            )

        if _rate_limited(ip):
            return _err(
                "Too many login attempts from your network. Wait 15 minutes and try again.",
                status=429,
            )
        if not email or not password:
            _record_failed_attempt(ip)
            return _err("Email and password are both required.")
        db = get_db(_output_dir_path())
        user = db.get_user_by_email(email)
        if user is None or not verify_password(password, user.password_hash):
            _record_failed_attempt(ip)
            return _err("Email or password is incorrect.", status=401)

        _clear_attempts(ip)
        token = db.create_session(user.id)
        response = RedirectResponse(url=next_url, status_code=303)
        _set_session_cookie(response, token)
        return response

    @app.post("/logout")
    def post_logout(request: Request):
        token = request.cookies.get(SESSION_COOKIE_NAME, "")
        if token:
            try:
                get_db(_output_dir_path()).delete_session(token)
            except Exception:  # noqa: BLE001
                pass
        response = RedirectResponse(url="/login", status_code=303)
        _clear_session_cookie(response)
        return response

    @app.get("/me")
    def api_me(request: Request):
        """Tiny JSON helper — the wireframe page can use this to render a
        sign-in chip without forcing a server-side template change."""
        user = getattr(request.state, "user", None)
        if user is None:
            return {"authenticated": False}
        return {"authenticated": True, "user": {"id": user.id, "email": user.email}}
