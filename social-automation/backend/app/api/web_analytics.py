"""Website analytics API for cloudless.gr owned properties."""
from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.user import Team, TeamMember, User
from app.models.web_analytics import WebAnalyticsConfig, WebAnalyticsEvent
from app.services.web_analytics import ingest_event

router = APIRouter()


class WebAnalyticsConfigIn(BaseModel):
    domain: str = Field(..., max_length=253)
    webhook_secret: str | None = Field(None, max_length=255)
    ga4_enabled: bool = False
    ga4_measurement_id: str | None = Field(None, max_length=40)
    ga4_api_secret: str | None = Field(None, max_length=100)
    plausible_enabled: bool = False
    plausible_domain: str | None = Field(None, max_length=253)
    plausible_api_url: str | None = Field(None, max_length=253)
    plausible_api_key: str | None = Field(None, max_length=255)
    meta_capi_enabled: bool = False
    meta_pixel_id: str | None = Field(None, max_length=40)
    meta_capi_access_token: str | None = Field(None, max_length=255)
    meta: dict[str, Any] | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


class WebAnalyticsConfigOut(BaseModel):
    id: str
    team_id: str
    domain: str
    ga4_enabled: bool
    ga4_measurement_id: str | None
    plausible_enabled: bool
    plausible_domain: str | None
    plausible_api_url: str | None
    meta_capi_enabled: bool
    meta_pixel_id: str | None
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WebEventIn(BaseModel):
    event: str = Field(..., max_length=100)
    domain: str = Field(..., max_length=253)
    path: str | None = Field(None, max_length=2048)
    session_id: str | None = Field(None, max_length=120)
    visitor_id: str | None = Field(None, max_length=120)
    referrer: str | None = Field(None, max_length=2048)
    locale: str | None = Field(None, max_length=10)
    timestamp: int | float | str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class WebEventOut(BaseModel):
    id: str
    event_name: str
    forwarded: dict[str, Any]


async def _get_team_for_user(db: AsyncSession, user: User) -> Team:
    result = await db.execute(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=404, detail="No team found for user")
    return team


def _verify_webhook_signature(payload: bytes, secret: str, signature: str | None) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.lower(), signature.lower().lstrip("sha256=").strip())


@router.post(
    "/webhooks/cloudless-analytics",
    response_model=WebEventOut,
    status_code=status.HTTP_201_CREATED,
)
async def receive_cloudless_event(
    request: Request,
    data: WebEventIn,
    db: AsyncSession = Depends(get_db),
) -> WebEventOut:
    """Public webhook endpoint called by cloudless.gr to send homepage events.

    Authentication is via HMAC-SHA256 signature in the X-Webhook-Signature header,
    compared against the configured WebAnalyticsConfig.webhook_secret for the domain.
    """
    signature = request.headers.get("x-webhook-signature")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")

    config_result = await db.execute(
        select(WebAnalyticsConfig).where(WebAnalyticsConfig.domain == data.domain)
    )
    config = config_result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Domain not configured")

    body = await request.body()
    if not _verify_webhook_signature(body, config.webhook_secret, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = await ingest_event(
        team_id=config.team_id,
        config=config,
        domain=data.domain,
        event_name=data.event,
        payload=data.payload,
        session_id=data.session_id,
        visitor_id=data.visitor_id,
        path=data.path,
        referrer=data.referrer,
        user_agent=request.headers.get("user-agent"),
        client_ip=request.client.host if request.client else None,
        locale=data.locale,
    )
    db.add(event)
    await db.commit()

    return WebEventOut(id=str(event.id), event_name=event.event_name, forwarded=event.forwarded)


@router.get("/configs", response_model=list[WebAnalyticsConfigOut])
async def list_configs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WebAnalyticsConfigOut]:
    team = await _get_team_for_user(db, current_user)
    result = await db.execute(
        select(WebAnalyticsConfig).where(WebAnalyticsConfig.team_id == team.id)
    )
    return [WebAnalyticsConfigOut.model_validate(c) for c in result.scalars().all()]


@router.post("/configs", response_model=WebAnalyticsConfigOut, status_code=status.HTTP_201_CREATED)
async def create_config(
    data: WebAnalyticsConfigIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WebAnalyticsConfigOut:
    team = await _get_team_for_user(db, current_user)

    existing = await db.execute(
        select(WebAnalyticsConfig).where(
            WebAnalyticsConfig.team_id == team.id,
            WebAnalyticsConfig.domain == data.domain,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Config for domain already exists")

    secret = data.webhook_secret or hashlib.sha256(str(datetime.now(UTC).timestamp()).encode()).hexdigest()
    config = WebAnalyticsConfig(
        team_id=team.id,
        domain=data.domain,
        webhook_secret=secret,
        ga4_enabled=data.ga4_enabled,
        ga4_measurement_id=data.ga4_measurement_id,
        ga4_api_secret=data.ga4_api_secret,
        plausible_enabled=data.plausible_enabled,
        plausible_domain=data.plausible_domain,
        plausible_api_url=data.plausible_api_url,
        plausible_api_key=data.plausible_api_key,
        meta_capi_enabled=data.meta_capi_enabled,
        meta_pixel_id=data.meta_pixel_id,
        meta_capi_access_token=data.meta_capi_access_token,
        meta=data.meta or {},
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return WebAnalyticsConfigOut.model_validate(config)


@router.put("/configs/{config_id}", response_model=WebAnalyticsConfigOut)
async def update_config(
    config_id: str,
    data: WebAnalyticsConfigIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WebAnalyticsConfigOut:
    team = await _get_team_for_user(db, current_user)
    result = await db.execute(
        select(WebAnalyticsConfig).where(
            WebAnalyticsConfig.id == config_id,
            WebAnalyticsConfig.team_id == team.id,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")

    config.domain = data.domain
    if data.webhook_secret:
        config.webhook_secret = data.webhook_secret
    config.ga4_enabled = data.ga4_enabled
    config.ga4_measurement_id = data.ga4_measurement_id
    config.ga4_api_secret = data.ga4_api_secret
    config.plausible_enabled = data.plausible_enabled
    config.plausible_domain = data.plausible_domain
    config.plausible_api_url = data.plausible_api_url
    config.plausible_api_key = data.plausible_api_key
    config.meta_capi_enabled = data.meta_capi_enabled
    config.meta_pixel_id = data.meta_pixel_id
    config.meta_capi_access_token = data.meta_capi_access_token
    if data.meta is not None:
        config.meta = data.meta
    config.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(config)
    return WebAnalyticsConfigOut.model_validate(config)


@router.delete("/configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(
    config_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    team = await _get_team_for_user(db, current_user)
    result = await db.execute(
        select(WebAnalyticsConfig).where(
            WebAnalyticsConfig.id == config_id,
            WebAnalyticsConfig.team_id == team.id,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    await db.delete(config)
    await db.commit()


class WebEventSummary(BaseModel):
    event_name: str
    count: int


class WebAnalyticsSummary(BaseModel):
    total_events: int
    top_events: list[WebEventSummary]


@router.get("/summary", response_model=WebAnalyticsSummary)
async def get_summary(
    days: int = 30,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WebAnalyticsSummary:
    team = await _get_team_for_user(db, current_user)
    since = datetime.now(UTC) - timedelta(days=days)

    total_result = await db.execute(
        select(func.count(WebAnalyticsEvent.id)).where(
            WebAnalyticsEvent.team_id == team.id,
            WebAnalyticsEvent.occurred_at >= since,
        )
    )
    total = int(total_result.scalar() or 0)

    top_result = await db.execute(
        select(WebAnalyticsEvent.event_name, func.count(WebAnalyticsEvent.id))
        .where(
            WebAnalyticsEvent.team_id == team.id,
            WebAnalyticsEvent.occurred_at >= since,
        )
        .group_by(WebAnalyticsEvent.event_name)
        .order_by(desc(func.count(WebAnalyticsEvent.id)))
        .limit(20)
    )
    top_events = [WebEventSummary(event_name=name, count=int(cnt)) for name, cnt in top_result.all()]

    return WebAnalyticsSummary(total_events=total, top_events=top_events)
