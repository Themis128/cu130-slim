"""Non-blocking AI usage logging.

Tracks provider/model calls for cost and Neuron observability. Logging failures
must never break the actual inference call.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import engine
from app.models.ai_usage import AIUsageLog

_usage_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """Very rough token estimate (≈ 0.75 words/token)."""
    words = len(text.split())
    return max(1, int(words * 1.33))


def _estimate_neurons(provider: str, model: str, prompt: str) -> int | None:
    """Rough Cloudflare Workers AI Neuron estimate; null for other providers."""
    if provider != "cloudflare":
        return None
    # Workers AI charges ~50-600 neurons per text prompt depending on model.
    # Image models: ~2,000-4,000 neurons per 1k step. Use conservative defaults.
    prompt_tokens = _estimate_tokens(prompt)
    if "flux" in model.lower() or "schnell" in model.lower():
        return 2000
    if "llama" in model.lower():
        return min(500, max(50, prompt_tokens * 2))
    return None


async def track_inference(
    provider: str,
    model: str,
    prompt: str,
    *,
    team_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    endpoint: str | None = None,
    latency_ms: int | None = None,
    success: bool = True,
    error: str | None = None,
    meta_data: dict[str, Any] | None = None,
) -> None:
    """Insert a usage log row, swallowing any DB or serialization errors."""
    try:
        log = AIUsageLog(
            team_id=team_id,
            user_id=user_id,
            provider=provider,
            model=model,
            endpoint=endpoint,
            prompt_length=len(prompt),
            estimated_tokens=_estimate_tokens(prompt),
            estimated_neurons=_estimate_neurons(provider, model, prompt),
            latency_ms=latency_ms,
            success=success,
            error=error,
            meta_data=meta_data or {},
        )
        # Persist in a short-lived session so usage logging never holds or
        # interferes with the caller's transaction.
        async with _usage_session() as s:
            s.add(log)
            await s.commit()
    except Exception as exc:
        logger.warning(f"Failed to track AI usage: {exc}")
