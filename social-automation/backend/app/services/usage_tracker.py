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
    actual_neurons: int | None = None,
    estimated_cost: float | None = None,
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
            actual_neurons=actual_neurons,
            estimated_cost=estimated_cost,
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
        # Check daily neuron quota (Phase 4.2) — best-effort alert, separate session
        if actual_neurons is not None and team_id is not None:
            await _check_daily_quota(team_id, provider, actual_neurons)
    except Exception as exc:
        logger.warning(f"Failed to track AI usage: {exc}")


async def get_daily_neuron_usage(team_id: uuid.UUID) -> int:
    """Return total neurons spent today for *team_id* across all providers."""
    from datetime import UTC, datetime

    from sqlalchemy import func, select

    from app.models.ai_usage import AIUsageLog

    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        async with _usage_session() as s:
            result = await s.execute(
                select(func.coalesce(func.sum(AIUsageLog.actual_neurons), 0)).where(
                    AIUsageLog.team_id == team_id,
                    AIUsageLog.created_at >= today_start,
                    AIUsageLog.success.is_(True),
                )
            )
            return int(result.scalar() or 0)
    except Exception as exc:
        logger.warning(f"Failed to query daily neuron usage: {exc}")
        return 0


async def _check_daily_quota(
    team_id: uuid.UUID,
    provider: str,
    neurons: int,
) -> None:
    """Warn when daily Cloudflare neuron spend exceeds 80% of the configured budget.

    The budget is read from the ``AIProvider.daily_neuron_budget`` column for the
    team's Cloudflare provider.  Alerts are logged — they do not block inference.
    """
    if provider != "cloudflare" or neurons <= 0:
        return
    try:
        from datetime import UTC, datetime

        from sqlalchemy import func, select

        from app.models.ai_provider import AIProvider
        from app.models.ai_usage import AIUsageLog

        async with _usage_session() as s:
            # Read budget
            budget_row = await s.execute(
                select(AIProvider.daily_neuron_budget).where(
                    AIProvider.team_id == team_id,
                    AIProvider.name == "cloudflare",
                )
            )
            budget = budget_row.scalar()
            if not budget or budget <= 0:
                return  # no budget configured

            today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            total_row = await s.execute(
                select(func.coalesce(func.sum(AIUsageLog.actual_neurons), 0)).where(
                    AIUsageLog.team_id == team_id,
                    AIUsageLog.created_at >= today_start,
                    AIUsageLog.success.is_(True),
                )
            )
            total = int(total_row.scalar() or 0)
            if total >= budget * 0.8:
                logger.warning(
                    "⚠️ Daily Cloudflare neuron usage for team %s: %d / %d (%.0f%%) — budget alert",
                    team_id, total, budget, (total / budget) * 100,
                )
    except Exception as exc:
        logger.warning(f"Quota check failed: {exc}")
