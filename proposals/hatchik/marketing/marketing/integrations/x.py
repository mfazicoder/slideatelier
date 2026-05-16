"""X (Twitter) API client.

Single-user OAuth 1.0a — appropriate for "the Hatchik founder marketing
the Hatchik product" (and later for each customer-tenant supplying
their own keys via marketing_tenant_api_keys in Phase 5).

Lazy-imports `tweepy` so the base package installs without it;
`pip install -e ".[distribute]"` adds it. Tests mock this module
wholesale and never touch tweepy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class MissingXCredentials(Exception):
    pass


@dataclass(frozen=True)
class XClient:
    consumer_key: str
    consumer_secret: str
    access_token: str
    access_token_secret: str

    _ENV_VARS = (
        "X_API_CONSUMER_KEY",
        "X_API_CONSUMER_SECRET",
        "X_API_ACCESS_TOKEN",
        "X_API_ACCESS_TOKEN_SECRET",
    )

    @classmethod
    def from_env(cls) -> "XClient":
        missing = [k for k in cls._ENV_VARS if not os.environ.get(k)]
        if missing:
            raise MissingXCredentials(
                f"missing X API env vars: {missing}. "
                "On prod they live in /etc/hatchik/signup.env."
            )
        return cls(*(os.environ[k] for k in cls._ENV_VARS))

    # ─── public API ────────────────────────────────────────────────────

    def post_tweet(self, text: str) -> dict[str, str]:
        """POST one tweet. Returns {id, url}. Raises on API error."""
        client = self._tweepy()
        resp = client.create_tweet(text=text)
        tweet_id = str(resp.data["id"])
        return {"id": tweet_id, "url": _tweet_url(tweet_id)}

    def post_thread(self, parts: list[str]) -> list[dict[str, str]]:
        """POST a thread as N replies. Returns [{id, url}, …] in order.
        On mid-thread failure the partial thread stays posted — the
        caller decides how to recover (e.g. record what made it)."""
        if not parts:
            raise ValueError("post_thread: parts list is empty")
        client = self._tweepy()
        out: list[dict[str, str]] = []
        reply_to: str | None = None
        for part in parts:
            kwargs: dict[str, object] = {"text": part}
            if reply_to is not None:
                kwargs["in_reply_to_tweet_id"] = reply_to
            resp = client.create_tweet(**kwargs)
            tweet_id = str(resp.data["id"])
            out.append({"id": tweet_id, "url": _tweet_url(tweet_id)})
            reply_to = tweet_id
        return out

    # ─── internals ─────────────────────────────────────────────────────

    def _tweepy(self):
        try:
            import tweepy  # noqa: WPS433 (deliberate lazy import)
        except ImportError as exc:
            raise RuntimeError(
                "tweepy is not installed. Run "
                "`pip install -e \".[distribute]\"` inside the marketing/ dir."
            ) from exc
        return tweepy.Client(
            consumer_key=self.consumer_key,
            consumer_secret=self.consumer_secret,
            access_token=self.access_token,
            access_token_secret=self.access_token_secret,
        )


def _tweet_url(tweet_id: str) -> str:
    # The `i/web/status/` form works for any account — no username needed.
    return f"https://x.com/i/web/status/{tweet_id}"
