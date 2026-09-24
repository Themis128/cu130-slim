"""LinkedIn Ads campaign snapshots — daily scrape of Campaign Manager metrics."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import Team


class AdCampaignSnapshot(Base):
    """Point-in-time metrics for a LinkedIn ad campaign, scraped from Campaign
    Manager via the LinkedIn browser sidecar (the Marketing API ads-reporting
    tier is not granted on this app).

    Appended daily by the ``linkedin-ads-report`` beat task so spend/engagement
    history is kept while a campaign runs.
    """

    __tablename__ = "ad_campaign_snapshots"
    __table_args__ = (
        Index("ix_ad_campaign_snapshots_team_time", "team_id", "captured_at"),
        Index("ix_ad_campaign_snapshots_campaign", "campaign_id", "captured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(30), nullable=False, default="linkedin")
    account_id: Mapped[str] = mapped_column(String(40), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(40), nullable=False)
    campaign_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")

    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    engagements: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spend_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    engagement_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cpc_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    budget_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    raw: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    team: Mapped[Team] = relationship("Team")
