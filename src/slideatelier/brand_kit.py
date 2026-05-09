"""Per-workspace BrandKit storage — JSON load/save with auth-aware paths.

A BrandKit lives at one of two paths under the configured output directory:

  - `<output>/brand_kit.json`                       — anonymous / dev fallback
  - `<output>/users/<user_id>/brand_kit.json`       — when a user is signed in

Routes resolve `user` via `getattr(request.state, "user", None)` and pass the
resulting user id (or None) to `brand_kit_path`. Anonymous traffic falls
through to the global file so the user's pre-auth kit keeps working.

The renderer reads the kit at render time (via ShapeRenderContext.brand_kit);
nothing here is cached — JSON load is cheap and it lets the published Web
Deck rebrand instantly after a save.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import BrandKit


def brand_kit_path(output_dir: Path, user_id: Optional[int | str] = None) -> Path:
    """Resolve the on-disk path for the kit JSON.

    Mirrors the per-user output layout used by the auth module: anonymous +
    SYSTEM_USER_ID land at the global file, anything else goes under
    `users/<id>/`.
    """
    if user_id is None:
        return output_dir / "brand_kit.json"
    # Treat the SYSTEM_USER_ID sentinel as anonymous so legacy decks keep
    # using the shared global kit. We import lazily to avoid forcing an
    # auth-package import for callers that don't care.
    try:
        from .auth import SYSTEM_USER_ID  # noqa: WPS433 — local import is intentional
    except Exception:  # noqa: BLE001 — auth package optional in some test envs
        SYSTEM_USER_ID = None  # type: ignore[assignment]
    if SYSTEM_USER_ID is not None and user_id == SYSTEM_USER_ID:
        return output_dir / "brand_kit.json"
    return output_dir / "users" / str(user_id) / "brand_kit.json"


def load_brand_kit(
    output_dir: Path, user_id: Optional[int | str] = None
) -> Optional[BrandKit]:
    """Load the saved kit. Returns None if no kit exists or the file is broken.

    A missing file is the common case (workspace hasn't completed onboarding)
    so it is NOT an error — callers treat None as "no overrides; use theme".

    Per-user lookups fall through to the global kit when no per-user kit
    exists, so a user without their own customisations still benefits from a
    workspace-wide default.
    """
    path = brand_kit_path(output_dir, user_id)
    if not path.exists():
        if user_id is not None:
            # Fall through to the global kit, if any.
            return load_brand_kit(output_dir, None)
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return BrandKit.model_validate(data)
    except Exception:  # noqa: BLE001 — corrupt JSON, fall back to "no kit"
        return None


def save_brand_kit(
    output_dir: Path,
    kit: BrandKit,
    user_id: Optional[int | str] = None,
) -> Path:
    """Persist the kit and return the path written.

    Creates parent directories as needed. The write is non-atomic — kits are
    small and the worst case (a half-written JSON) is read-back returning
    None, which the renderer already handles by falling back to theme values.
    """
    path = brand_kit_path(output_dir, user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(kit.model_dump_json(indent=2))
    return path
