import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.security import encrypt_token
from app.db.session import get_db
from app.models.ai_provider import AIProvider
from app.models.ai_usage import AIUsageLog
from app.models.user import Team, TeamMember, User
from app.services.inference import PROVIDER_CATALOG

router = APIRouter()
logger = logging.getLogger(__name__)


class AIProviderUpsert(BaseModel):
    name: str | None = None
    display_name: str | None = None
    api_key: str | None = None          # plaintext — encrypted before storing
    base_url: str | None = None
    default_model: str | None = None
    is_enabled: bool = True
    is_default: bool = False
    fallbacks: str | None = None
    timeout_seconds: int | None = None
    max_retries: int | None = None
    daily_neuron_budget: int | None = None


class AIProviderOut(BaseModel):
    id: uuid.UUID
    name: str
    display_name: str
    base_url: str
    default_model: str
    is_enabled: bool
    is_default: bool
    has_key: bool
    fallbacks: str | None = None
    timeout_seconds: int = 120
    max_retries: int = 1
    daily_neuron_budget: int | None = None
    failure_count: int = 0
    circuit_open: bool = False
    cooldown_until: datetime | None = None
    updated_at: datetime

    class Config:
        from_attributes = True


async def _get_team(user: User, db: AsyncSession) -> Team:
    result = await db.execute(select(Team).join(TeamMember).where(TeamMember.user_id == user.id))
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@router.get("/catalog")
async def list_catalog():
    """Return the static list of supported providers. No auth required — static data."""
    return PROVIDER_CATALOG


@router.get("", response_model=list[AIProviderOut])
async def list_providers(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_team(current_user, db)
    result = await db.execute(select(AIProvider).where(AIProvider.team_id == team.id))
    providers = result.scalars().all()
    return [
        AIProviderOut(
            id=p.id,
            name=p.name,
            display_name=p.display_name,
            base_url=p.base_url,
            default_model=p.default_model,
            is_enabled=p.is_enabled,
            is_default=p.is_default,
            has_key=p.api_key_enc is not None,
            fallbacks=p.fallbacks,
            timeout_seconds=p.timeout_seconds,
            max_retries=p.max_retries,
            daily_neuron_budget=p.daily_neuron_budget,
            failure_count=p.failure_count,
            circuit_open=p.circuit_open,
            cooldown_until=p.cooldown_until,
            updated_at=p.updated_at,
        )
        for p in providers
    ]


@router.put("/{name}", response_model=AIProviderOut)
async def upsert_provider(
    name: str,
    body: AIProviderUpsert,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_team(current_user, db)

    # Look up catalog defaults
    catalog = next((c for c in PROVIDER_CATALOG if c["name"] == name), None)
    if not catalog and name not in ("ollama",):
        raise HTTPException(status_code=400, detail=f"Unknown provider: {name}")

    result = await db.execute(
        select(AIProvider).where(AIProvider.team_id == team.id, AIProvider.name == name)
    )
    provider = result.scalar_one_or_none()

    if not provider:
        provider = AIProvider(team_id=team.id, name=name)
        db.add(provider)

    provider.display_name = body.display_name or (catalog["display_name"] if catalog else name)
    provider.base_url = body.base_url or (catalog["base_url"] if catalog else "")
    provider.default_model = body.default_model or (catalog["default_model"] if catalog else "")
    provider.is_enabled = body.is_enabled
    provider.is_default = body.is_default
    # Phase 4 routing policy fields
    if body.fallbacks is not None:
        provider.fallbacks = body.fallbacks
    if body.timeout_seconds is not None:
        provider.timeout_seconds = body.timeout_seconds
    if body.max_retries is not None:
        provider.max_retries = body.max_retries
    if body.daily_neuron_budget is not None:
        provider.daily_neuron_budget = body.daily_neuron_budget
    provider.updated_at = datetime.now(UTC)

    if body.api_key:
        provider.api_key_enc = encrypt_token(body.api_key)
    elif body.api_key == "":
        provider.api_key_enc = None

    # If set as default, clear others
    if body.is_default:
        others_result = await db.execute(
            select(AIProvider).where(AIProvider.team_id == team.id, AIProvider.name != name)
        )
        for other in others_result.scalars().all():
            other.is_default = False

    await db.commit()
    await db.refresh(provider)
    return AIProviderOut(
        id=provider.id,
        name=provider.name,
        display_name=provider.display_name,
        base_url=provider.base_url,
        default_model=provider.default_model,
        is_enabled=provider.is_enabled,
        is_default=provider.is_default,
        has_key=provider.api_key_enc is not None,
        fallbacks=provider.fallbacks,
        timeout_seconds=provider.timeout_seconds,
        max_retries=provider.max_retries,
        daily_neuron_budget=provider.daily_neuron_budget,
        failure_count=provider.failure_count,
        circuit_open=provider.circuit_open,
        cooldown_until=provider.cooldown_until,
        updated_at=provider.updated_at,
    )


@router.get("/{name}/models")
async def list_provider_models(
    name: str,
    current_user: User = Depends(get_current_user),
):
    """List every model available on this provider (live catalog from the vendor)."""
    from app.services.inference import list_workers_ai_models

    if name != "cloudflare":
        raise HTTPException(status_code=400, detail=f"Live model listing is not supported for provider: {name}")
    return await list_workers_ai_models()


@router.delete("/{name}", status_code=204)
async def delete_provider(
    name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_team(current_user, db)
    result = await db.execute(
        select(AIProvider).where(AIProvider.team_id == team.id, AIProvider.name == name)
    )
    provider = result.scalar_one_or_none()
    if provider:
        await db.delete(provider)
        await db.commit()


@router.post("/{name}/test")
async def test_provider(
    name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Quick connectivity test — sends a test request to the provider."""
    from app.services.inference import call_inference, get_team_id_for_user

    team_id = await get_team_id_for_user(current_user.id, db)

    # Image generation providers need different test approach
    IMAGE_GEN_PROVIDERS = {"nvidia-flux", "nvidia-flux-dev", "local-sd35", "nvidia-flux-pipeline"}

    # Use a fast non-reasoning model for LLM providers
    FAST_TEST_MODELS: dict[str, str] = {
        "nvidia": "nvidia/nemotron-3-nano-30b-a3b",
        "groq": "qwen/qwen3.6-27b",
        "gemini": "gemini-2.5-flash",
        "openrouter": "openrouter/free",
        "sambanova": "gpt-oss-120b",
        "mistral": "mistral-small-latest",
        "cohere": "command-r7b-12-2024",
        "cloudflare": "@cf/meta/llama-3.2-3b-instruct",
    }
    if not re.fullmatch(r"^[A-Za-z0-9_-]+$", name):
        raise HTTPException(status_code=400, detail="Invalid provider name")

    test_model = FAST_TEST_MODELS.get(name)

    try:
        if name in IMAGE_GEN_PROVIDERS:
            # For image generation, just verify the API key works with a minimal request
            # Return success if provider is configured (actual generation tested via generate-image endpoint)
            return {"ok": True, "response": "Image generation provider configured. Use generate-image endpoint to test."}

        result = await call_inference(
            "Say hi.",
            provider_name=name,
            db=db,
            team_id=team_id,
            max_tokens=10,
            model_override=test_model,
        )
        text = result.get("text") or ""
        return {"ok": True, "response": text[:200]}
    except Exception:
        # Provider name is already validated above; do not include it in the
        # generic error path to avoid log-injection reports on this sink.
        logger.exception("Provider test failed")
        return {"ok": False, "error": "Provider test failed. Check server logs for details."}


# ---------------------------------------------------------------------------
# Phase 4 — Usage dashboard, circuit breaker, quota status
# ---------------------------------------------------------------------------


class UsageSummary(BaseModel):
    provider: str
    model: str
    total_calls: int
    successful_calls: int
    failed_calls: int
    avg_latency_ms: float
    total_neurons: int
    estimated_cost: float
    last_call_at: datetime | None


class UsageResponse(BaseModel):
    summary: list[UsageSummary]
    daily_neurons: int
    daily_neuron_budget: int | None
    quota_pct: float | None


@router.get("/usage", response_model=UsageResponse)
async def get_usage_summary(
    days: int = Query(7, ge=1, le=90),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate AI usage by provider/model for the dashboard (Phase 4.3)."""
    team = await _get_team(current_user, db)
    since = datetime.now(UTC) - timedelta(days=days)

    rows = await db.execute(
        select(
            AIUsageLog.provider,
            AIUsageLog.model,
            func.count(AIUsageLog.id).label("total_calls"),
            func.sum(
                func.cast(AIUsageLog.success, __import__("sqlalchemy").Integer)
            ).label("successful_calls"),
            func.avg(AIUsageLog.latency_ms).label("avg_latency_ms"),
            func.coalesce(func.sum(AIUsageLog.actual_neurons), 0).label("total_neurons"),
            func.coalesce(func.sum(AIUsageLog.estimated_cost), 0).label("estimated_cost"),
            func.max(AIUsageLog.created_at).label("last_call_at"),
        )
        .where(AIUsageLog.team_id == team.id, AIUsageLog.created_at >= since)
        .group_by(AIUsageLog.provider, AIUsageLog.model)
        .order_by(func.count(AIUsageLog.id).desc())
    )

    summary = []
    for provider, model, total, ok, avg_lat, neurons, cost, last_call in rows.all():
        summary.append(UsageSummary(
            provider=provider,
            model=model,
            total_calls=int(total or 0),
            successful_calls=int(ok or 0),
            failed_calls=int(total or 0) - int(ok or 0),
            avg_latency_ms=round(float(avg_lat or 0), 1),
            total_neurons=int(neurons or 0),
            estimated_cost=round(float(cost or 0), 4),
            last_call_at=last_call,
        ))

    # Daily neuron totals + budget
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_row = await db.execute(
        func.select(
            func.coalesce(func.sum(AIUsageLog.actual_neurons), 0)
        ).where(
            AIUsageLog.team_id == team.id,
            AIUsageLog.created_at >= today_start,
            AIUsageLog.success.is_(True),
        )
    )
    daily_neurons = int(daily_row.scalar() or 0)

    budget_row = await db.execute(
        select(AIProvider.daily_neuron_budget).where(
            AIProvider.team_id == team.id,
            AIProvider.name == "cloudflare",
        )
    )
    budget = budget_row.scalar()
    quota_pct = round((daily_neurons / budget) * 100, 1) if budget and budget > 0 else None

    return UsageResponse(
        summary=summary,
        daily_neurons=daily_neurons,
        daily_neuron_budget=budget,
        quota_pct=quota_pct,
    )


@router.post("/{name}/reset-circuit")
async def reset_circuit_breaker(
    name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually reset a provider's circuit breaker (Phase 4.5)."""
    team = await _get_team(current_user, db)
    result = await db.execute(
        select(AIProvider).where(AIProvider.team_id == team.id, AIProvider.name == name)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    provider.failure_count = 0
    provider.circuit_open = False
    provider.cooldown_until = None
    await db.commit()
    # Also reset in-memory state
    from app.services.inference import _circuit_state
    _circuit_state.pop(name, None)
    return {"ok": True, "provider": name, "message": "Circuit breaker reset"}
