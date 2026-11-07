"""LinkedIn Ad Library intelligence — competitor/public ad observations.

Populated by the ``linkedin-ad-library-watch`` beat task from
``GET /rest/adLibrary`` (Default Tier, apps 265077368 + 264925843).
One row per observed ad (upserted by ``ad_url``); ``status`` flips to
``ended`` when an ad disappears from results across consecutive runs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import Team


class LinkedInAdIntel(Base):
    __tablename__ = "linkedin_ad_intel"
    __table_args__ = (
        Index("ix_linkedin_ad_intel_advertiser", "advertiser"),
        Index("ix_linkedin_ad_intel_status_seen", "status", "last_seen_at"),
        Index("ix_linkedin_ad_intel_team_time", "team_id", "last_seen_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ad_url: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    advertiser: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    ad_payer: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    advertiser_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    is_restricted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    impressions_from: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    impressions_to: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_impression_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_impression_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    countries: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    targeting: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    last_keyword: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    miss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    team: Mapped[Team | None] = relationship("Team")


class LinkedInIntelWatch(Base):
    """Watchlist entries the ad-library beat task searches each run."""

    __tablename__ = "linkedin_intel_watch"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # keyword | advertiser
    value: Mapped[str] = mapped_column(String(300), nullable=False)
    country: Mapped[str] = mapped_column(String(10), nullable=False, default="GR")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
