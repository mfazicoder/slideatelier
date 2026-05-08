import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Config(BaseModel):
    anthropic_api_key: str = ""
    model: str = "claude-opus-4-7"
    output_dir: Path = Path("./output")
    templates_dir: Path = Path("./templates")
    cache_dir: Path = Path("./cache")
    cache_enabled: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        cache_enabled = os.getenv("SLIDEATELIER_CACHE_ENABLED", "true").lower() not in ("false", "0", "no")
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
            model=os.getenv("SLIDEATELIER_MODEL", "claude-opus-4-7"),
            output_dir=Path(os.getenv("SLIDEATELIER_OUTPUT_DIR", "./output")),
            templates_dir=Path(os.getenv("SLIDEATELIER_TEMPLATES_DIR", "./templates")),
            cache_dir=Path(os.getenv("SLIDEATELIER_CACHE_DIR", "./cache")),
            cache_enabled=cache_enabled,
        )

    def require_api_key(self) -> str:
        """Call this from commands that need to hit the API."""
        if not self.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Copy .env.example to .env and fill it in."
            )
        return self.anthropic_api_key
