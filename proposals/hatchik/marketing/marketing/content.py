"""Content draft schemas + marketing_content_queue CRUD.

The content agent (Layer 2) returns one `ContentDraft` per call. We
store `body` as the canonical post-text and keep format-specific
structure in `metadata` (thread parts, blog sections, email subject/
preview, etc.) so the approval UI can render rich previews without
reparsing the body.

Channel taxonomy matches `marketing_content_queue.channel` CHECK
constraint: x_tweet, x_thread, linkedin, blog, email, reddit_draft,
discord_draft. Reddit + Discord are draft-only — never auto-posted.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


Channel = Literal[
    "x_tweet", "x_thread", "linkedin", "blog", "email", "reddit_draft", "discord_draft"
]
ContentStatus = Literal["pending", "approved", "rejected", "scheduled", "posted", "failed"]


# ─── per-format structured metadata payloads ────────────────────────────


class XTweetMeta(BaseModel):
    """Single tweet — body is the tweet text."""
    pass


class XThreadMeta(BaseModel):
    parts: list[str] = Field(min_length=2, max_length=15)

    @field_validator("parts")
    @classmethod
    def parts_under_280(cls, v: list[str]) -> list[str]:
        too_long = [i for i, p in enumerate(v) if len(p) > 280]
        if too_long:
            raise ValueError(f"thread parts {too_long} exceed 280 chars")
        return v


class LinkedInMeta(BaseModel):
    pass  # body holds the full post text


class BlogSection(BaseModel):
    heading: str
    bullets: list[str] = Field(min_length=2)


class BlogOutlineMeta(BaseModel):
    title: str
    hook: str
    sections: list[BlogSection] = Field(min_length=3, max_length=7)
    key_takeaway: str


class EmailMeta(BaseModel):
    subject: str
    preview: str
    cta: str


# ─── unified draft (what the LLM returns + what we queue) ───────────────


class ContentDraft(BaseModel):
    channel: Channel
    body: str = Field(min_length=8)
    pillar: str
    angle_hook: str
    metadata: dict[str, Any] = {}

    @model_validator(mode="after")
    def validate_metadata_for_channel(self) -> "ContentDraft":
        if self.channel == "x_tweet":
            if len(self.body) > 280:
                raise ValueError(f"x_tweet body {len(self.body)} > 280 chars")
            XTweetMeta.model_validate(self.metadata or {})
        elif self.channel == "x_thread":
            XThreadMeta.model_validate(self.metadata)
        elif self.channel == "linkedin":
            if not (300 <= len(self.body) <= 3000):
                raise ValueError(f"linkedin body length {len(self.body)} not in [300, 3000]")
        elif self.channel == "blog":
            BlogOutlineMeta.model_validate(self.metadata)
        elif self.channel == "email":
            EmailMeta.model_validate(self.metadata)
        return self


class DraftParseError(Exception):
    pass


def parse_draft(raw: str) -> ContentDraft:
    """Parse an LLM response into a ContentDraft. Tolerant of ``` fences."""
    text = raw.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[: -3].rstrip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DraftParseError(f"invalid JSON: {exc}") from exc
    try:
        return ContentDraft.model_validate(data)
    except ValidationError as exc:
        raise DraftParseError(f"schema violation: {exc}") from exc


# ─── marketing_content_queue helpers ────────────────────────────────────


def insert_draft(
    conn: sqlite3.Connection,
    *,
    tenant_id: int,
    draft: ContentDraft,
    source_run_id: int | None,
    scheduled_for: str | None = None,
) -> int:
    metadata = dict(draft.metadata or {})
    metadata.setdefault("pillar", draft.pillar)
    metadata.setdefault("angle_hook", draft.angle_hook)

    cur = conn.execute(
        """
        INSERT INTO marketing_content_queue
            (tenant_id, channel, body, metadata_json, status,
             scheduled_for, source_run_id, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            tenant_id,
            draft.channel,
            draft.body,
            json.dumps(metadata, ensure_ascii=False),
            scheduled_for,
            source_run_id,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def list_queue(
    conn: sqlite3.Connection,
    *,
    tenant_id: int,
    status: ContentStatus | None = None,
    limit: int = 20,
) -> list[sqlite3.Row]:
    if status is None:
        return conn.execute(
            """
            SELECT id, channel, status, body, metadata_json, created_at, posted_at,
                   scheduled_for, rejection_reason
            FROM marketing_content_queue
            WHERE tenant_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (tenant_id, limit),
        ).fetchall()
    return conn.execute(
        """
        SELECT id, channel, status, body, metadata_json, created_at, posted_at,
               scheduled_for, rejection_reason
        FROM marketing_content_queue
        WHERE tenant_id = ? AND status = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (tenant_id, status, limit),
    ).fetchall()


def get_item(
    conn: sqlite3.Connection, *, tenant_id: int, item_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, channel, status, body, metadata_json, created_at, posted_at,
               scheduled_for, rejection_reason, source_run_id, parent_id
        FROM marketing_content_queue
        WHERE tenant_id = ? AND id = ?
        """,
        (tenant_id, item_id),
    ).fetchone()


def _transition(
    conn: sqlite3.Connection,
    *,
    tenant_id: int,
    item_id: int,
    expected_from: tuple[str, ...],
    new_status: ContentStatus,
    rejection_reason: str | None = None,
) -> bool:
    """State-machine transition: only succeed if current status is in
    expected_from. Returns True if a row was updated."""
    placeholders = ",".join("?" * len(expected_from))
    params: list[Any] = [new_status]
    if new_status == "rejected":
        params.append(rejection_reason)
    params.extend([tenant_id, item_id, *expected_from])
    set_clause = "status = ?" + (", rejection_reason = ?" if new_status == "rejected" else "")
    cur = conn.execute(
        f"""
        UPDATE marketing_content_queue
        SET {set_clause}
        WHERE tenant_id = ? AND id = ? AND status IN ({placeholders})
        """,
        params,
    )
    return cur.rowcount > 0


def approve(conn: sqlite3.Connection, *, tenant_id: int, item_id: int) -> bool:
    return _transition(
        conn, tenant_id=tenant_id, item_id=item_id,
        expected_from=("pending",), new_status="approved",
    )


def reject(
    conn: sqlite3.Connection, *, tenant_id: int, item_id: int, reason: str
) -> bool:
    return _transition(
        conn, tenant_id=tenant_id, item_id=item_id,
        expected_from=("pending",), new_status="rejected",
        rejection_reason=reason,
    )


def queue_stats(conn: sqlite3.Connection, *, tenant_id: int) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
        FROM marketing_content_queue
        WHERE tenant_id = ?
        GROUP BY status
        """,
        (tenant_id,),
    ).fetchall()
    return {r["status"]: int(r["n"]) for r in rows}
