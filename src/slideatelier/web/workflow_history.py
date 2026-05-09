"""Per-workflow snapshot history for undo/redo + workflow listing.

Each workflow lives at <output_dir>/workflow/<job_id>/. We keep snapshots of
the current deck.json (and storyboard.json) at <job_dir>/history/ so the user
can step backward and forward through their edits.

Storage layout:
  <job_dir>/history/deck-<ts>.json       — historical deck snapshots
  <job_dir>/history/storyboard-<ts>.json — historical storyboard snapshots
  <job_dir>/history/pointer.json — {"deck_history": [...], "deck_position": N, ...}

Snapshot capping: 50 entries per artifact. Beyond that, oldest is dropped.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

ArtifactKind = Literal["deck", "storyboard"]
HISTORY_CAP = 50


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def _history_dir(job_dir: Path) -> Path:
    p = job_dir / "history"
    p.mkdir(exist_ok=True)
    return p


def _pointer_path(job_dir: Path) -> Path:
    return _history_dir(job_dir) / "pointer.json"


def _read_pointer(job_dir: Path) -> dict:
    p = _pointer_path(job_dir)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            pass
    return {
        "deck_history": [],
        "deck_position": -1,
        "storyboard_history": [],
        "storyboard_position": -1,
    }


def _write_pointer(job_dir: Path, data: dict) -> None:
    _pointer_path(job_dir).write_text(json.dumps(data, indent=2))


def _artifact_path(job_dir: Path, kind: ArtifactKind) -> Path:
    return job_dir / f"{kind}.json"


def _snapshot_path(job_dir: Path, kind: ArtifactKind, ts: str) -> Path:
    return _history_dir(job_dir) / f"{kind}-{ts}.json"


def _meta_path(job_dir: Path, kind: ArtifactKind, ts: str) -> Path:
    """Sidecar meta file path: holds {label, prompt, ai_summary, parent_timestamp}."""
    return _history_dir(job_dir) / f"{kind}-{ts}.meta.json"


def _default_label(kind: ArtifactKind, ts: str) -> str:
    """Best-effort `Manual edit · HH:MM` from the timestamp string."""
    try:
        dt = datetime.strptime(ts[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return f"Manual {kind} edit · {dt.strftime('%H:%M')}"
    except Exception:  # noqa: BLE001
        return f"Manual {kind} edit"


def _read_meta(job_dir: Path, kind: ArtifactKind, ts: str) -> dict:
    """Read the sidecar meta for a snapshot. Returns default-shaped dict on missing/corrupt."""
    p = _meta_path(job_dir, kind, ts)
    default = {
        "label": _default_label(kind, ts),
        "prompt": None,
        "ai_summary": None,
        "parent_timestamp": None,
    }
    if not p.exists():
        return default
    try:
        loaded = json.loads(p.read_text())
        if not isinstance(loaded, dict):
            return default
        # Merge so missing keys don't break callers
        for k, v in default.items():
            loaded.setdefault(k, v)
        return loaded
    except Exception:  # noqa: BLE001
        return default


def _write_meta(
    job_dir: Path,
    kind: ArtifactKind,
    ts: str,
    *,
    label: str,
    prompt: str | None = None,
    ai_summary: str | None = None,
    parent_timestamp: str | None = None,
) -> None:
    payload = {
        "label": label,
        "prompt": prompt,
        "ai_summary": ai_summary,
        "parent_timestamp": parent_timestamp,
    }
    _meta_path(job_dir, kind, ts).write_text(json.dumps(payload, indent=2))


def snapshot(
    job_dir: Path,
    kind: ArtifactKind,
    *,
    label: str | None = None,
    prompt: str | None = None,
    parent_timestamp: str | None = None,
) -> str | None:
    """Snapshot the current artifact (deck or storyboard) into history.

    Strategy: append the CURRENT version (before mutation) to history. So the
    sequence of snapshots represents the timeline of saved states; the head of
    the list is the most-recently-saved state.

    If the user has undone some edits (position < tail), the forward branch is
    truncated — undone changes can't be redone after a fresh edit.

    Optional kwargs (all None-default; existing call sites stay unchanged):
        label:            human-readable name for the version card. Defaults to
                          `"Manual <kind> edit · HH:MM"`.
        prompt:           the AI-edit prompt that produced this snapshot, if any.
                          The first 60 chars become `ai_summary` automatically —
                          NO extra Claude calls.
        parent_timestamp: when restoring an old card, the source timestamp this
                          snapshot was forked from. Used to render the tree.

    Returns the snapshot's timestamp, or None if there's nothing to snapshot.
    """
    src = _artifact_path(job_dir, kind)
    if not src.exists():
        return None
    ts = _now_ts()
    snap = _snapshot_path(job_dir, kind, ts)
    snap.write_text(src.read_text())

    # Sidecar meta — written eagerly so list_snapshots() always returns label/etc.
    final_label = label if label else _default_label(kind, ts)
    ai_summary: str | None = None
    if prompt:
        clean = prompt.strip()
        ai_summary = clean[:60] + ("…" if len(clean) > 60 else "") if clean else None
    _write_meta(
        job_dir,
        kind,
        ts,
        label=final_label,
        prompt=prompt,
        ai_summary=ai_summary,
        parent_timestamp=parent_timestamp,
    )

    pointer = _read_pointer(job_dir)
    history_key = f"{kind}_history"
    pos_key = f"{kind}_position"
    history: list[str] = pointer.get(history_key, [])
    pos: int = pointer.get(pos_key, -1)

    # Truncate forward branch if user is mid-history
    if pos < len(history) - 1:
        for stale in history[pos + 1:]:
            stale_path = _snapshot_path(job_dir, kind, stale)
            if stale_path.exists():
                stale_path.unlink()
            stale_meta = _meta_path(job_dir, kind, stale)
            if stale_meta.exists():
                stale_meta.unlink()
        history = history[: pos + 1]

    history.append(ts)
    pos = len(history) - 1

    # Cap history size: drop oldest if over CAP
    if len(history) > HISTORY_CAP:
        excess = len(history) - HISTORY_CAP
        for old_ts in history[:excess]:
            old_path = _snapshot_path(job_dir, kind, old_ts)
            if old_path.exists():
                old_path.unlink()
            old_meta = _meta_path(job_dir, kind, old_ts)
            if old_meta.exists():
                old_meta.unlink()
        history = history[excess:]
        pos = len(history) - 1

    pointer[history_key] = history
    pointer[pos_key] = pos
    _write_pointer(job_dir, pointer)
    return ts


def can_undo(job_dir: Path, kind: ArtifactKind) -> bool:
    pointer = _read_pointer(job_dir)
    return pointer.get(f"{kind}_position", -1) > 0


def can_redo(job_dir: Path, kind: ArtifactKind) -> bool:
    pointer = _read_pointer(job_dir)
    pos = pointer.get(f"{kind}_position", -1)
    history = pointer.get(f"{kind}_history", [])
    return pos < len(history) - 1


def undo(job_dir: Path, kind: ArtifactKind) -> bool:
    """Restore the artifact to the previous snapshot. Returns True if an undo
    actually happened, False if at the start of history."""
    pointer = _read_pointer(job_dir)
    history: list[str] = pointer.get(f"{kind}_history", [])
    pos: int = pointer.get(f"{kind}_position", -1)
    if pos <= 0:
        return False
    target_ts = history[pos - 1]
    snap = _snapshot_path(job_dir, kind, target_ts)
    if not snap.exists():
        return False
    _artifact_path(job_dir, kind).write_text(snap.read_text())
    pointer[f"{kind}_position"] = pos - 1
    _write_pointer(job_dir, pointer)
    return True


def list_snapshots(job_dir: Path) -> list[dict]:
    """Return all snapshots across both kinds, newest-first.

    Each entry: {kind, timestamp, iso, epoch, is_current, label, ai_summary,
    prompt, parent_timestamp}. Used to build the cross-stage time-travel
    timeline AND the version-cards rail (which renders the branching tree).
    """
    pointer = _read_pointer(job_dir)
    out: list[dict] = []
    for kind in ("deck", "storyboard"):
        history: list[str] = pointer.get(f"{kind}_history", [])
        pos: int = pointer.get(f"{kind}_position", -1)
        for i, ts in enumerate(history):
            try:
                # ts is "YYYYMMDDTHHMMSSffffff"; parse to ISO
                dt = datetime.strptime(ts[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
                iso = dt.isoformat()
                epoch = dt.timestamp()
            except Exception:  # noqa: BLE001
                iso = ts
                epoch = 0.0
            meta = _read_meta(job_dir, kind, ts)  # type: ignore[arg-type]
            out.append({
                "kind": kind,
                "timestamp": ts,
                "iso": iso,
                "epoch": epoch,
                "is_current": i == pos,
                "label": meta.get("label") or _default_label(kind, ts),  # type: ignore[arg-type]
                "ai_summary": meta.get("ai_summary"),
                "prompt": meta.get("prompt"),
                "parent_timestamp": meta.get("parent_timestamp"),
            })
    # Sort by timestamp string (which includes sub-second precision in the
    # microsecond suffix) — sorting by epoch alone collapses sub-second
    # timestamps generated in the same second into insertion order.
    out.sort(key=lambda e: e["timestamp"], reverse=True)
    return out


def restore_snapshot(job_dir: Path, kind: ArtifactKind, timestamp: str) -> bool:
    """Restore the named artifact to the snapshot at the given timestamp.

    Forking-on-restore: the restore is implemented as a fresh snapshot at the
    head of history with `parent_timestamp=timestamp` and a default label of
    `"Forked from <ts>"`. The on-disk history stays linear, but the tree view
    surfaces branches by following `parent_timestamp` links.
    """
    pointer = _read_pointer(job_dir)
    history: list[str] = pointer.get(f"{kind}_history", [])
    if timestamp not in history:
        return False
    snap = _snapshot_path(job_dir, kind, timestamp)
    if not snap.exists():
        return False
    # Write the snapshot's content to the live artifact, then snapshot the new
    # state so undo/redo continues to work AND the fork is recorded with a
    # parent_timestamp pointer for the tree-view UI.
    _artifact_path(job_dir, kind).write_text(snap.read_text())
    fork_label = f"Forked from {timestamp}"
    snapshot(job_dir, kind, label=fork_label, parent_timestamp=timestamp)
    return True


def rename_snapshot(job_dir: Path, kind: ArtifactKind, timestamp: str, new_label: str) -> bool:
    """Update the human-readable label on an existing snapshot's sidecar meta.
    Returns True on success, False if the snapshot doesn't exist.
    """
    pointer = _read_pointer(job_dir)
    history: list[str] = pointer.get(f"{kind}_history", [])
    if timestamp not in history:
        return False
    snap = _snapshot_path(job_dir, kind, timestamp)
    if not snap.exists():
        return False
    new_label = (new_label or "").strip()
    if not new_label:
        new_label = _default_label(kind, timestamp)
    meta = _read_meta(job_dir, kind, timestamp)
    _write_meta(
        job_dir,
        kind,
        timestamp,
        label=new_label,
        prompt=meta.get("prompt"),
        ai_summary=meta.get("ai_summary"),
        parent_timestamp=meta.get("parent_timestamp"),
    )
    return True


def read_snapshot_content(job_dir: Path, kind: ArtifactKind, timestamp: str) -> dict | None:
    """Return the JSON contents of a snapshot, or None if not found.
    Used by the side-by-side compare endpoint to render two timestamps."""
    pointer = _read_pointer(job_dir)
    history: list[str] = pointer.get(f"{kind}_history", [])
    if timestamp not in history:
        return None
    snap = _snapshot_path(job_dir, kind, timestamp)
    if not snap.exists():
        return None
    try:
        return json.loads(snap.read_text())
    except Exception:  # noqa: BLE001
        return None


def redo(job_dir: Path, kind: ArtifactKind) -> bool:
    """Restore the artifact to the next snapshot in the redo branch."""
    pointer = _read_pointer(job_dir)
    history: list[str] = pointer.get(f"{kind}_history", [])
    pos: int = pointer.get(f"{kind}_position", -1)
    if pos >= len(history) - 1:
        return False
    target_ts = history[pos + 1]
    snap = _snapshot_path(job_dir, kind, target_ts)
    if not snap.exists():
        return False
    _artifact_path(job_dir, kind).write_text(snap.read_text())
    pointer[f"{kind}_position"] = pos + 1
    _write_pointer(job_dir, pointer)
    return True


# ---------------------------------------------------------------------------
# Workflow listing for the file-management UI
# ---------------------------------------------------------------------------

def list_workflows(workflows_root: Path, include_archived: bool = False) -> list[dict]:
    """Return metadata for every workflow on disk, newest first.

    Each entry: {job_id, title, subtitle, stage, status, slide_count,
    last_modified_ts, last_modified_iso, has_pptx, archived}.

    Archived workflows live under <workflows_root>/.archive/<job_id>/. When
    include_archived=True, those are walked too and tagged with archived=True.
    """
    if not workflows_root.exists():
        return []
    out: list[dict] = []
    for job_dir in workflows_root.iterdir():
        if not job_dir.is_dir():
            continue
        if job_dir.name.startswith("."):
            continue
        info = describe_workflow(job_dir)
        if info is not None:
            info["archived"] = False
            out.append(info)
    if include_archived:
        archive_root = workflows_root / ".archive"
        if archive_root.exists() and archive_root.is_dir():
            for job_dir in archive_root.iterdir():
                if not job_dir.is_dir():
                    continue
                info = describe_workflow(job_dir)
                if info is not None:
                    info["archived"] = True
                    out.append(info)
    out.sort(key=lambda e: e["last_modified_ts"], reverse=True)
    return out


def describe_workflow(job_dir: Path) -> dict | None:
    """Best-effort summary of a single workflow. Falls back gracefully if
    deck.json or storyboard.json are missing/corrupt."""
    if not job_dir.exists():
        return None

    title = "(untitled)"
    subtitle = ""
    slide_count = 0
    deck_path = job_dir / "deck.json"
    sb_path = job_dir / "storyboard.json"
    title_override_path = job_dir / "title_override.txt"

    # Title preference: title_override > deck > storyboard > brief snippet
    if deck_path.exists():
        try:
            data = json.loads(deck_path.read_text())
            title = data.get("title") or title
            subtitle = data.get("subtitle") or ""
            slide_count = len(data.get("slides", []))
        except Exception:  # noqa: BLE001
            pass
    elif sb_path.exists():
        try:
            data = json.loads(sb_path.read_text())
            title = data.get("title") or title
            subtitle = data.get("subtitle") or ""
            slide_count = len(data.get("slides", []))
        except Exception:  # noqa: BLE001
            pass

    if title == "(untitled)":
        brief_path = job_dir / "brief.txt"
        if brief_path.exists():
            snippet = brief_path.read_text().strip().splitlines()[0:1]
            if snippet:
                title = snippet[0][:80] + ("…" if len(snippet[0]) > 80 else "")

    # title_override.txt takes precedence over everything if present and non-empty
    if title_override_path.exists():
        try:
            override = title_override_path.read_text().strip()
            if override:
                title = override
        except Exception:  # noqa: BLE001
            pass

    status: dict = {}
    sp = job_dir / "status.json"
    if sp.exists():
        try:
            status = json.loads(sp.read_text())
        except Exception:  # noqa: BLE001
            pass

    # Last modified = most recent mtime among the artifact files
    candidates = [job_dir / n for n in ("deck.json", "storyboard.json", "status.json", "brief.txt")]
    mtimes = [c.stat().st_mtime for c in candidates if c.exists()]
    last_ts = max(mtimes) if mtimes else job_dir.stat().st_mtime

    has_pptx = (job_dir / "deck.pptx").exists()
    has_deck = deck_path.exists()
    has_storyboard = sb_path.exists()

    # Stage marker: prefer status.json, else infer from files
    stage = status.get("stage") or (
        "hi_fi" if has_pptx else "wireframe" if has_deck else "storyboard" if has_storyboard else "new"
    )

    return {
        "job_id": job_dir.name,
        "title": title,
        "subtitle": subtitle,
        "slide_count": slide_count,
        "stage": stage,
        "status": status.get("status", ""),
        "has_pptx": has_pptx,
        "has_deck": has_deck,
        "has_storyboard": has_storyboard,
        "last_modified_ts": last_ts,
        "last_modified_iso": datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat(),
    }
