"""Resend transactional-email client.

Minimal — POST /emails with httpx. Uses RESEND_API_KEY env var. The
sender ("from") defaults to onboarding@resend.dev for local dev; in
prod set RESEND_FROM_EMAIL to a verified sender on your Resend domain.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class MissingResendKey(Exception):
    pass


class ResendError(Exception):
    pass


_RESEND_URL = "https://api.resend.com/emails"


def send_email(
    *,
    to: str | list[str],
    subject: str,
    text: str,
    from_email: str | None = None,
    reply_to: str | None = None,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Send one email via Resend. Returns the parsed JSON response
    (which includes the message id)."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        raise MissingResendKey(
            "RESEND_API_KEY not set. Populate proposals/hatchik/marketing/.env "
            "or /etc/hatchik/signup.env on the VPS."
        )
    sender = from_email or os.environ.get("RESEND_FROM_EMAIL") or "onboarding@resend.dev"
    payload: dict[str, Any] = {
        "from": sender,
        "to": [to] if isinstance(to, str) else to,
        "subject": subject,
        "text": text,
    }
    if reply_to:
        payload["reply_to"] = reply_to

    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    if resp.status_code >= 400:
        raise ResendError(f"Resend HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()
