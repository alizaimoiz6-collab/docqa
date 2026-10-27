"""Runtime settings, read once from environment variables."""

import os
from dataclasses import dataclass
from functools import lru_cache


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    model: str = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
    effort: str = os.environ.get("ANTHROPIC_EFFORT", "medium")
    max_output_tokens: int = _int("ANTHROPIC_MAX_TOKENS", 8000)
    # Seconds per Claude request, and how many times the SDK retries 429/5xx/timeouts.
    request_timeout: float = float(os.environ.get("ANTHROPIC_TIMEOUT", 90))
    max_retries: int = _int("ANTHROPIC_MAX_RETRIES", 2)

    max_upload_mb: int = _int("MAX_UPLOAD_MB", 20)
    chunk_chars: int = _int("CHUNK_CHARS", 1200)
    # Documents under this many characters (~25k tokens, ~50 pages) are sent whole;
    # larger ones are narrowed to the top_k most relevant chunks first.
    context_char_budget: int = _int("CONTEXT_CHAR_BUDGET", 100_000)
    top_k_chunks: int = _int("TOP_K_CHUNKS", 40)

    # Per-client caps, to protect the API key on a public URL.
    ask_rate_limit_per_min: int = _int("ASK_RATE_LIMIT_PER_MIN", 10)
    upload_rate_limit_per_min: int = _int("UPLOAD_RATE_LIMIT_PER_MIN", 5)
    # Total Claude spend allowed per UTC day across all visitors; 0 disables the cap.
    daily_budget_usd: float = float(os.environ.get("DAILY_BUDGET_USD", 1.0))

    # "memory" for local dev; "modal" shares documents across Modal containers.
    store_backend: str = os.environ.get("DOCQA_STORE", "memory")


@lru_cache
def get_settings() -> Settings:
    return Settings()
