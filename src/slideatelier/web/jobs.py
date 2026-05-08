"""In-memory job tracker. Single-process, single-user. Add Redis when we scale."""
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

JobStatus = Literal["queued", "running", "done", "failed"]


@dataclass
class Job:
    id: str
    status: JobStatus = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    progress_message: str = ""
    deck_path: Path | None = None
    spec_path: Path | None = None
    title: str = ""
    slide_count: int = 0
    error: str | None = None
    cache_hit: bool = False
    duration_seconds: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "progress_message": self.progress_message,
            "deck_path": str(self.deck_path) if self.deck_path else None,
            "spec_path": str(self.spec_path) if self.spec_path else None,
            "title": self.title,
            "slide_count": self.slide_count,
            "error": self.error,
            "cache_hit": self.cache_hit,
            "duration_seconds": self.duration_seconds,
        }


class JobStore:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for k, v in fields.items():
                setattr(job, k, v)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)


# Singleton — module-level store. Single-process app, so this is fine.
store = JobStore()


def run_generate_job(
    job_id: str,
    content: str,
    requirements: str,
    template_name: str | None,
    output_dir: Path,
    regenerate: bool = False,
) -> None:
    """Worker function — runs in a background thread via FastAPI BackgroundTasks.
    Imports the engine inside the function so the web layer doesn't pull
    Anthropic SDK into module scope."""
    from ..claude_client import make_client
    from ..config import Config
    from ..metadata import GeneratedDeck
    from ..planner import plan_deck
    from ..renderer import DeckRenderer
    from ..template import load_default_template, load_template

    try:
        store.update(job_id, status="running", started_at=datetime.now(timezone.utc),
                     progress_message="Loading template…")

        config = Config.from_env()
        config.require_api_key()

        if template_name:
            t_path = config.templates_dir / f"{template_name}.json"
            tpl = load_template(t_path)
        else:
            tpl = load_default_template(config.templates_dir)

        msg = "Checking cache…" if not regenerate else f"Forcing fresh generation with {config.model}…"
        store.update(job_id, progress_message=msg)

        client = make_client(config)
        deck, metadata = plan_deck(client, config, content, requirements, regenerate=regenerate)

        cache_label = " (from cache)" if metadata.cache_hit else ""
        store.update(
            job_id,
            progress_message=f"Rendering deck{cache_label}…",
            title=deck.title,
            slide_count=len(deck.slides),
            cache_hit=metadata.cache_hit,
            duration_seconds=metadata.duration_seconds,
        )

        deck_path = output_dir / f"{job_id}.pptx"
        spec_path = output_dir / f"{job_id}.json"
        DeckRenderer(tpl, config.templates_dir).render(deck, deck_path)
        wrapped = GeneratedDeck(metadata=metadata, deck=deck)
        spec_path.write_text(wrapped.model_dump_json(indent=2))

        store.update(
            job_id,
            status="done",
            completed_at=datetime.now(timezone.utc),
            progress_message="Done",
            deck_path=deck_path,
            spec_path=spec_path,
        )
    except Exception as e:  # noqa: BLE001
        store.update(
            job_id,
            status="failed",
            completed_at=datetime.now(timezone.utc),
            error=f"{type(e).__name__}: {e}",
            progress_message=f"Failed: {e}",
        )
        # Log full trace to stderr for debugging
        traceback.print_exc()
