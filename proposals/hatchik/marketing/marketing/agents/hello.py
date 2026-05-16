"""Phase-0 smoke agent.

Renders the hello prompt, asks Opus 4.7 for a one-sentence positioning
line for Hatchik, logs the run, returns the line. Used to verify the
whole pipeline end-to-end (env → DB → budget → LLM → run logging) with
the smallest possible prompt surface.
"""

from __future__ import annotations

from pathlib import Path

from .. import anthropic_client, db, prompts, schema, tenant


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRIEF_PATH = _REPO_ROOT.parent / "PRODUCT_OFFERING.md"


def _load_brief(path: Path, max_chars: int = 1200) -> str:
    text = path.read_text(encoding="utf-8")
    # Take the first §1 "The product, in one sentence" + §2 intro chunk.
    # 1200 chars is more than the prompt needs; keeps token cost low.
    return text[:max_chars]


def run(tenant_slug: str = "hatchik", brief_path: Path | None = None) -> dict:
    conn = db.connect()
    try:
        schema.ensure_schema(conn)
        t = tenant.get_by_slug(conn, tenant_slug)

        prompt = prompts.load("hello")
        prompts.mirror_to_db(conn, prompt)

        brief = _load_brief(brief_path or DEFAULT_BRIEF_PATH)
        user_message = (
            "<product>\n"
            f"{brief}\n"
            "</product>\n\n"
            "Write the positioning sentence now."
        )

        result = anthropic_client.complete(
            conn,
            tenant_id=t.id,
            tenant_cap_usd=t.spend_cap_daily_usd,
            layer="hello",
            model=prompt.model,
            system=prompt.body,
            user_message=user_message,
            max_tokens=int(prompt.params.get("max_tokens", 256)),
            prompt_name=prompt.name,
            prompt_version=prompt.version,
        )
        return result
    finally:
        conn.close()
