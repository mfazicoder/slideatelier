"""Brief Inbox — Sprint W.

Fourth entry route: paste a Slack thread / Notion brief / email / Google Doc URL
+ optional file attachments → slideAtelier emits a draft deck plus a
structured `BriefAnalysis` so the user can see what the LLM heard from the
brief BEFORE editing the storyboard.

This module owns:
1. URL extraction — pulls plain text from any http(s) URL detected in a paste,
   best-effort. 5 second timeout per URL, no scraping bypass; sites that block
   anonymous bots are silently skipped.
2. Attachment extraction — reads `.pdf` (pdfplumber), `.docx` (python-docx),
   and plain `.txt`/`.md` into UTF-8 text. The PDF/DOCX libraries are imported
   lazily so the rest of the app keeps working when they're not installed.
3. Concatenation — produces a single `brief_text` plus a `sources` list noting
   where each chunk came from, written to disk as `brief.txt` /
   `brief_sources.json` so the review UI and later workflow stages can render
   provenance.
4. Claude call — `analyze_brief()` produces (Storyboard, BriefAnalysis) in a
   SINGLE call. Storyboard generation reuses `plan_storyboard`; BriefAnalysis
   is a separate parse() call against `BriefAnalysis`. The HTTP route runs
   them sequentially and persists both as JSON.

Hard rules from the sprint brief:
- DO NOT touch any auth/ internals (use getattr(request.state, "user", None)
  in the route layer; this module is pure logic).
- Imports for pdfplumber / python-docx are LAZY — tests don't need them
  installed because we never call into them with real attachments in tests.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import httpx

from .claude_client import make_client
from .config import Config
from .metadata import PROMPT_VERSION, GenerationMetadata
from .models import BriefAnalysis
from .storyboard import Storyboard, plan_storyboard

# ---------------------------------------------------------------------------
# Constants — kept high-up so the route layer can import them too
# ---------------------------------------------------------------------------

URL_FETCH_TIMEOUT_SEC = 5.0
"""Per-URL httpx timeout. Fail-fast: a single slow site shouldn't hold up
ingestion. Five seconds is enough for 99% of public pages."""

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
"""Per-file upload cap (5MB). Enforced at the route layer; included here as
the canonical limit so docs / tests share the same constant."""

ALLOWED_ATTACHMENT_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}
"""Whitelist of attachment extensions we know how to extract text from."""

# Match any http(s) URL — we extract URLs from pastes so users can drop a
# Notion / Google Doc / Slack thread link directly in the textarea.
_URL_RE = re.compile(r"https?://[^\s<>\)\]]+", re.IGNORECASE)

# Hosts that need auth to read content — we recognize them and skip rather
# than emit garbage HTML or Google's signin redirect text into the brief.
_AUTH_GATED_HOSTS = (
    "docs.google.com",
    "drive.google.com",
    "notion.so",
    "www.notion.so",
)


# ---------------------------------------------------------------------------
# Source attribution
# ---------------------------------------------------------------------------

@dataclass
class BriefSource:
    """A chunk of the assembled brief plus where it came from. Persisted as
    `brief_sources.json` next to the workflow's `brief.txt`."""

    kind: str  # "paste" | "url" | "attachment"
    label: str  # human-readable origin (URL, filename, or "pasted text")
    char_count: int
    note: str = ""  # optional — e.g. "skipped: auth required" or fetch error

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "char_count": self.char_count,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# URL extraction
# ---------------------------------------------------------------------------

def extract_urls(text: str) -> list[str]:
    """Find http(s) URLs in `text`, deduped, in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;:!?")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _is_auth_gated(url: str) -> bool:
    lowered = url.lower()
    return any(host in lowered for host in _AUTH_GATED_HOSTS)


def _strip_html(html: str) -> str:
    """Best-effort HTML → plain text. We don't pull in BeautifulSoup just for
    this; a regex strip is good enough for the inbox preview. Drops <script>
    and <style> blocks first, then collapses whitespace."""
    no_blocks = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL
    )
    no_tags = re.sub(r"<[^>]+>", " ", no_blocks)
    # decode the most common entities; fall back to leaving them in
    no_tags = (
        no_tags.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return re.sub(r"\s+", " ", no_tags).strip()


def fetch_url_text(url: str, *, timeout: float = URL_FETCH_TIMEOUT_SEC) -> tuple[str, str]:
    """Fetch a URL and return (text, note). On failure or auth-gated host,
    returns ("", explanation). Always succeeds in returning a tuple — the
    caller decides whether to include the chunk in the brief.
    """
    if _is_auth_gated(url):
        return "", "skipped: requires authentication"
    try:
        # Bot-friendly UA. Sites that block anonymous traffic still get a
        # response we can treat as "best-effort"; we don't try to bypass.
        headers = {"User-Agent": "slideAtelier-brief-inbox/0.1 (+https://slideatelier.app)"}
        with httpx.Client(
            timeout=timeout, follow_redirects=True, headers=headers
        ) as client:
            resp = client.get(url)
        if resp.status_code >= 400:
            return "", f"fetch failed: HTTP {resp.status_code}"
        text = resp.text or ""
        # Strip HTML if the response looks like markup
        ct = (resp.headers.get("content-type") or "").lower()
        if "html" in ct or text.lstrip().startswith("<"):
            text = _strip_html(text)
        text = text.strip()
        if not text:
            return "", "fetch returned empty body"
        return text, ""
    except httpx.TimeoutException:
        return "", f"timeout after {timeout:.0f}s"
    except httpx.HTTPError as e:
        return "", f"fetch error: {type(e).__name__}"
    except Exception as e:  # noqa: BLE001 — best-effort; never raise to caller
        return "", f"fetch error: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Attachment extraction
# ---------------------------------------------------------------------------

def extract_attachment_text(filename: str, data: bytes) -> tuple[str, str]:
    """Extract text from `data` based on `filename`'s extension.

    Returns `(text, note)`. `note` carries the reason when extraction
    skipped/failed (e.g. "unsupported format", "pdfplumber not installed"),
    so the user sees attribution rather than silent loss.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_ATTACHMENT_SUFFIXES:
        return "", f"skipped: unsupported format {suffix or '(none)'}"

    if suffix in (".txt", ".md"):
        try:
            return data.decode("utf-8", errors="replace").strip(), ""
        except Exception as e:  # noqa: BLE001
            return "", f"decode error: {type(e).__name__}"

    if suffix == ".pdf":
        try:
            import pdfplumber  # type: ignore[import-not-found]
        except ImportError:
            return "", "pdfplumber not installed"
        try:
            from io import BytesIO

            buf = BytesIO(data)
            chunks: list[str] = []
            with pdfplumber.open(buf) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        chunks.append(page_text.strip())
            return "\n\n".join(chunks).strip(), ""
        except Exception as e:  # noqa: BLE001
            return "", f"pdf parse error: {type(e).__name__}"

    if suffix == ".docx":
        try:
            import docx  # type: ignore[import-not-found]
        except ImportError:
            return "", "python-docx not installed"
        try:
            from io import BytesIO

            buf = BytesIO(data)
            d = docx.Document(buf)
            paragraphs = [p.text for p in d.paragraphs if p.text.strip()]
            return "\n".join(paragraphs).strip(), ""
        except Exception as e:  # noqa: BLE001
            return "", f"docx parse error: {type(e).__name__}"

    return "", f"skipped: unsupported format {suffix}"


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def assemble_brief(
    pasted_content: str,
    attachments: Iterable[tuple[str, bytes]] = (),
    *,
    fetch_urls: bool = True,
) -> tuple[str, list[BriefSource]]:
    """Build a single `brief_text` string + ordered `sources` list.

    Order: pasted text first, then expanded URL bodies (best-effort), then
    each attachment's extracted text. Sources are listed in the SAME ORDER
    the chunks appear so a reader can match a paragraph to its origin.

    `fetch_urls=False` skips the network round-trip — useful for tests and
    for when the brief looks URL-free (the route layer doesn't bother).
    """
    parts: list[str] = []
    sources: list[BriefSource] = []

    pasted = (pasted_content or "").strip()
    if pasted:
        parts.append(pasted)
        sources.append(
            BriefSource(kind="paste", label="pasted text", char_count=len(pasted))
        )

    if fetch_urls and pasted:
        for url in extract_urls(pasted):
            text, note = fetch_url_text(url)
            if text:
                parts.append(f"[Source: {url}]\n{text}")
                sources.append(
                    BriefSource(
                        kind="url", label=url, char_count=len(text), note=""
                    )
                )
            else:
                # Attribute the skipped URL anyway so the user can see why
                # it wasn't included.
                sources.append(
                    BriefSource(kind="url", label=url, char_count=0, note=note)
                )

    for filename, data in attachments:
        text, note = extract_attachment_text(filename, data)
        if text:
            parts.append(f"[Source: {filename}]\n{text}")
            sources.append(
                BriefSource(
                    kind="attachment",
                    label=filename,
                    char_count=len(text),
                    note="",
                )
            )
        else:
            sources.append(
                BriefSource(
                    kind="attachment", label=filename, char_count=0, note=note
                )
            )

    return "\n\n".join(parts), sources


def write_sources(job_dir: Path, sources: list[BriefSource]) -> None:
    """Persist the source list to `<job_dir>/brief_sources.json`."""
    job_dir.mkdir(parents=True, exist_ok=True)
    payload = [s.to_dict() for s in sources]
    (job_dir / "brief_sources.json").write_text(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Claude call: BriefAnalysis (storyboard reuses plan_storyboard from storyboard.py)
# ---------------------------------------------------------------------------

ANALYSIS_SYSTEM_PROMPT = """You are a senior management consulting partner reading a raw brief BEFORE it becomes a deck. Your job is to surface what the brief asks for and what it leaves out — so the deck author can confirm or correct your read.

# YOUR TASK

Given a raw brief (a paste of a Slack thread, email, Notion page, or extracted attachments), produce a `BriefAnalysis` with:

- `stated_goals`: what the brief explicitly says it wants the deck to do. Use the brief's own words where possible. 1–5 short items.
- `audience`: who the deck is for and the decision context. Single string, ≤30 words. Empty string if the brief truly gives no signal.
- `key_messages`: the argument lines the deck must land. Each in takeaway form (verb + object), not topic form. 2–5 items.
- `risks_unaddressed`: gaps the brief leaves implicit — missing data, ambiguous scope, likely audience pushback that isn't pre-empted. 0–5 items.

# RULES

1. Be specific. "Audience: executives" is bad; "Audience: 5-person founding team deciding on Series A timing this quarter" is good.
2. `key_messages` are the deck's CONCLUSIONS — what the audience should walk away believing. Not the agenda. "Reallocate $2M from SMB to enterprise" not "Discuss budget".
3. `risks_unaddressed` is the value-add: what would a great consultant FLAG to the brief author?  Vague KPIs, missing financial impact, conflicting goals, audience-mismatch.
4. Do NOT invent facts the brief doesn't support. If the audience is unclear, say so via empty `audience` and an entry in `risks_unaddressed`.

Return only the BriefAnalysis."""


def analyze_brief(
    config: Config,
    brief_text: str,
    requirements: str = "",
) -> tuple[Storyboard, BriefAnalysis, GenerationMetadata]:
    """Run Storyboard + BriefAnalysis off the same brief.

    The two artifacts power the review UI: Storyboard is the deck draft, and
    BriefAnalysis is the read-out so the user sees what the model HEARD.

    The two calls run sequentially (not in parallel) for code simplicity —
    they're both fast (~5–8 s each on Opus) and we want to fail clearly if
    either errors. Sprint W's quality bar is wow factor, not p99 latency.
    """
    config.require_api_key()

    storyboard, story_meta = plan_storyboard(
        make_client(config), config, brief_text, requirements
    )

    started = time.monotonic()
    client = make_client(config)
    user_prompt = f"# Source brief\n\n{brief_text}"
    if requirements:
        user_prompt += f"\n\n# Additional requirements\n\n{requirements}"
    user_prompt += "\n\nRead the brief and produce the BriefAnalysis."

    response = client.messages.parse(
        model=config.model,
        max_tokens=4000,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": ANALYSIS_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
        output_format=BriefAnalysis,
    )
    duration = time.monotonic() - started

    if response.parsed_output is None:
        raise RuntimeError("BriefAnalysis output failed schema validation")

    analysis_meta = GenerationMetadata(
        model_id=config.model,
        model_response_id=getattr(response, "model", None),
        prompt_version=PROMPT_VERSION,
        cache_hit=False,
        input_hash="",
        duration_seconds=duration,
    )
    # Story meta carries the input hash and duration; analysis meta is
    # discarded by the route, but we return it for completeness/tests.
    _ = story_meta
    return storyboard, response.parsed_output, analysis_meta
