import time

from anthropic import Anthropic
from rich.console import Console

from .cache import DeckCache, compute_input_hash
from .claude_client import SYSTEM_PROMPT
from .config import Config
from .metadata import PROMPT_VERSION, GenerationMetadata
from .models import SlideDeck

console = Console()


def plan_deck(
    client: Anthropic,
    config: Config,
    content: str,
    requirements: str = "",
    *,
    regenerate: bool = False,
) -> tuple[SlideDeck, GenerationMetadata]:
    """Turn a content brief + optional requirements into a structured slide deck spec.

    Returns (deck, metadata). When the input hash matches a cached entry and
    `regenerate` is False, returns the cached deck instantly with metadata
    indicating cache_hit=True. Otherwise calls the API and caches the result."""

    input_hash = compute_input_hash(
        system_prompt=SYSTEM_PROMPT,
        brief=content,
        requirements=requirements,
        model_id=config.model,
        prompt_version=PROMPT_VERSION,
    )

    cache = DeckCache(config.cache_dir, enabled=config.cache_enabled)

    if not regenerate:
        cached = cache.get(input_hash)
        if cached is not None:
            console.print(f"[dim]Cache hit ({input_hash[:8]}…) — returning cached deck[/dim]")
            return cached, GenerationMetadata(
                model_id=config.model,
                prompt_version=PROMPT_VERSION,
                cache_hit=True,
                input_hash=input_hash,
                duration_seconds=None,
            )

    user_prompt = f"Source content:\n\n{content}"
    if requirements:
        user_prompt += f"\n\nAdditional requirements:\n{requirements}"
    user_prompt += "\n\nDesign the slide deck. Return the structured plan."

    started = time.monotonic()
    response = client.messages.parse(
        model=config.model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
        output_format=SlideDeck,
    )
    duration = time.monotonic() - started

    cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
    if cache_read:
        console.print(f"[dim]Anthropic prompt cache hit: {cache_read} tokens[/dim]")
    elif cache_write:
        console.print(f"[dim]Anthropic prompt cache write: {cache_write} tokens[/dim]")

    if response.parsed_output is None:
        raise RuntimeError(
            "Model output did not validate against SlideDeck schema. "
            "Try simplifying the brief or check the response."
        )

    deck = response.parsed_output
    cache.set(input_hash, deck)

    metadata = GenerationMetadata(
        model_id=config.model,
        model_response_id=getattr(response, "model", None),
        prompt_version=PROMPT_VERSION,
        cache_hit=False,
        input_hash=input_hash,
        duration_seconds=duration,
    )
    return deck, metadata
