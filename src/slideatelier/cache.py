"""Response cache — guarantees that identical inputs return identical decks.

Cache key = SHA256(system_prompt + brief + requirements + model + prompt_version).
Cache value = the SlideDeck JSON only (no metadata; metadata is per-generation).

Single-process file-based cache. Replace with Redis when we go multi-process."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .models import SlideDeck


def compute_input_hash(
    *,
    system_prompt: str,
    brief: str,
    requirements: str,
    model_id: str,
    prompt_version: str,
) -> str:
    """SHA256 of all inputs that should produce identical outputs.
    Template is intentionally NOT included — it affects rendering, not planning."""
    h = hashlib.sha256()
    for piece in (system_prompt, brief, requirements, model_id, prompt_version):
        h.update(piece.encode("utf-8"))
        h.update(b"\x00")  # delimiter — prevents collisions like ("a", "bc") vs ("ab", "c")
    return h.hexdigest()


class DeckCache:
    def __init__(self, cache_dir: Path, enabled: bool = True):
        self.cache_dir = cache_dir
        self.enabled = enabled
        if self.enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, key: str) -> SlideDeck | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return SlideDeck.model_validate_json(path.read_text())
        except Exception:  # noqa: BLE001
            # Corrupt cache entry; ignore and regenerate
            return None

    def set(self, key: str, deck: SlideDeck) -> None:
        if not self.enabled:
            return
        self._path(key).write_text(deck.model_dump_json(indent=2))

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_entries(self) -> list[dict]:
        """Return cache entries with metadata, newest first."""
        if not self.cache_dir.exists():
            return []
        out = []
        for path in self.cache_dir.glob("*.json"):
            stat = path.stat()
            try:
                deck = SlideDeck.model_validate_json(path.read_text())
                title = deck.title
                slide_count = len(deck.slides)
            except Exception:  # noqa: BLE001
                title = "(invalid)"
                slide_count = 0
            out.append({
                "key": path.stem,
                "title": title,
                "slide_count": slide_count,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            })
        out.sort(key=lambda e: e["modified_at"], reverse=True)
        return out

    def clear(self) -> int:
        """Remove all cache entries. Returns count of removed files."""
        if not self.cache_dir.exists():
            return 0
        count = 0
        for path in self.cache_dir.glob("*.json"):
            path.unlink()
            count += 1
        return count
