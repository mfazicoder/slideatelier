"""PostHog event-tracking wrapper.

No-op if the key isn't set or the library isn't installed. Analytics
must never break the distribution pipeline, so every call here is
fire-and-forget — exceptions are swallowed.
"""

from __future__ import annotations

import os
from typing import Any


def capture(
    *,
    distinct_id: str,
    event: str,
    properties: dict[str, Any] | None = None,
) -> None:
    api_key = os.environ.get("POSTHOG_API_KEY", "")
    host = os.environ.get("POSTHOG_HOST", "https://eu.posthog.com")
    if not api_key:
        return
    try:
        import posthog as ph  # noqa: WPS433
    except ImportError:
        return
    try:
        ph.api_key = api_key
        ph.host = host
        ph.capture(distinct_id=distinct_id, event=event, properties=properties or {})
    except Exception:
        # Never let analytics break a real-world post.
        return
