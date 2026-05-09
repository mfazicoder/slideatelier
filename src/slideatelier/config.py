import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

logger = logging.getLogger(__name__)


# Env vars that MUST be set when SLIDEATELIER_ENV=production. Each one has a
# dev-mode fallback elsewhere (Config.from_env or app code) so existing tests
# and local dev keep working without ceremony.
PRODUCTION_REQUIRED_ENV = (
    "ANTHROPIC_API_KEY",
    "SLIDEATELIER_OUTPUT_DIR",
    "SLIDEATELIER_TEMPLATES_DIR",
    "DOMAIN",
    "SESSION_SECRET",
)


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


def validate_production_env() -> list[str]:
    """Validate required env vars based on SLIDEATELIER_ENV.

    In production: any missing var raises SystemExit so the container fails
    fast (Fly/Render/Caddy will surface the error). In dev (the default):
    log a warning and continue.

    Returns the list of missing var names — useful for tests.
    """
    env = os.getenv("SLIDEATELIER_ENV", "development").lower()
    missing = [name for name in PRODUCTION_REQUIRED_ENV if not os.getenv(name, "").strip()]

    if env == "production":
        if missing:
            # Structured log line — easy to grep in Fly/Render log tails.
            logger.error(
                "startup_validation_failed env=production missing=%s",
                ",".join(missing),
            )
            # Plain stderr too — guarantees visibility before logging is wired.
            print(
                f"[slideatelier] FATAL: SLIDEATELIER_ENV=production but missing required env vars: {missing}",
                file=sys.stderr,
            )
            raise SystemExit(2)
        logger.info("startup_validation_ok env=production")
    else:
        if missing:
            logger.warning(
                "startup_validation_warning env=%s missing=%s (dev mode — continuing)",
                env,
                ",".join(missing),
            )
    return missing
