"""Website analytics ingestion for cloudless.gr and other owned web properties.

Events arrive via webhook from the site, are persisted, and optionally forwarded
 to GA4, Plausible, and Meta Conversions API so SocialAuto can act as the
 central marketing-data router for the brand.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import Team


class WebAnalyticsConfig(Base):
    """Per-tenant configuration for forwarding website events to analytics sinks."""

    __tablename__ = "web_analytics_configs"
    __table_args__ = (
        Index("ix_web_analytics_configs_team", "team_id"),
        Index("ix_web_analytics_configs_domain", "domain"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    webhook_secret: Mapped[str] = mapped_column(String(255), nullable=False)

    # Google Analytics 4 — Measurement Protocol
    ga4_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    ga4_measurement_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ga4_api_secret: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Plausible — events API
    plausible_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    plausible_domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    plausible_api_url: Mapped[str | None] = mapped_column(
        String(253), nullable=True, default="https://plausible.io/api/event"
    )
    plausible_api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Meta Conversions API
    meta_capi_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    meta_pixel_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    meta_capi_access_token: Mapped[str | None] = mapped_column(String(255), nullable=True)

    meta: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    team: Mapped[Team] = relationship("Team")


class WebAnalyticsEvent(Base):
    """Raw website event received from an owned property."""

    __tablename__ = "web_analytics_events"
    __table_args__ = (
        Index("ix_web_analytics_events_team_time", "team_id", "occurred_at"),
        Index("ix_web_analytics_events_domain_time", "domain", "occurred_at"),
        Index("ix_web_analytics_events_name", "event_name"),
        Index("ix_web_analytics_events_session", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    config_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("web_analytics_configs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    event_name: Mapped[str] = mapped_column(String(100), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    visitor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    referrer: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    locale: Mapped[str | None] = mapped_column(String(10), nullable=True)

    payload: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    # Forwarding results per sink: {"ga4": {"ok": true}, "plausible": {...}, ...}
    forwarded: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    team: Mapped[Team] = relationship("Team")
    config: Mapped[WebAnalyticsConfig | None] = relationship("WebAnalyticsConfig")
