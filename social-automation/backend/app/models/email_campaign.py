"""Email campaign models — 1-to-many marketing/survey sends to CRM contacts."""

from __future__ import annotations

import enum
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CampaignStatus(enum.StrEnum):
    draft = "draft"
    sending = "sending"
    sent = "sent"
    partial = "partial"  # some recipients failed


class CampaignKind(enum.StrEnum):
    marketing = "marketing"
    survey = "survey"


class RecipientStatus(enum.StrEnum):
    pending = "pending"
    sent = "sent"
    failed = "failed"
    skipped_suppressed = "skipped_suppressed"  # on the do-not-contact list
    unsubscribed = "unsubscribed"  # clicked unsubscribe for THIS campaign send


class EmailCampaign(Base):
    __tablename__ = "email_campaigns"
    __table_args__ = (
        Index("ix_email_campaigns_team_created", "team_id", "created_at"),
        Index("ix_email_campaigns_team_status", "team_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[CampaignKind] = mapped_column(
        SQLEnum(CampaignKind, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=CampaignKind.marketing,
    )
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    text_body: Mapped[str] = mapped_column(Text, nullable=False)
    html_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_name: Mapped[str] = mapped_column(String(120), nullable=False, default="Cloudless")
    reply_to: Mapped[str | None] = mapped_column(String(320), nullable=True)

    status: Mapped[CampaignStatus] = mapped_column(
        SQLEnum(CampaignStatus, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=CampaignStatus.draft,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Optional EspoCRM target list used to resolve recipients.
    espocrm_target_list_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Optional funnel link (e.g. a Nextcloud Forms public survey URL).
    survey_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    meta_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class EmailRecipient(Base):
    """One intended delivery per campaign. token doubles as the unsubscribe key."""

    __tablename__ = "email_recipients"
    __table_args__ = (
        Index("ix_email_recipients_campaign", "campaign_id"),
        Index("ix_email_recipients_status", "campaign_id", "status"),
        Index("ix_email_recipients_team_email", "team_id", "email"),
        UniqueConstraint("campaign_id", "email", name="uq_email_recipients_campaign_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("email_campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[RecipientStatus] = mapped_column(
        SQLEnum(RecipientStatus, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=RecipientStatus.pending,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)

    @staticmethod
    def new_token() -> str:
        return secrets.token_urlsafe(32)


class SurveyResponse(Base):
    """One funnel submission — tied to the campaign + recipient that invited
    the respondent, so the CRM/datalake can trace the conversion path."""

    __tablename__ = "survey_responses"
    __table_args__ = (
        Index("ix_survey_responses_campaign", "campaign_id"),
        Index("ix_survey_responses_team", "team_id", "submitted_at"),
        UniqueConstraint("recipient_id", name="uq_survey_responses_recipient"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("email_campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipient_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("email_recipients.id", ondelete="SET NULL"),
        nullable=True,
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)

    # Answer map keyed by question id from campaign.meta_data["questions"].
    answers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    espocrm_noted: Mapped[bool] = mapped_column(nullable=False, default=False)

    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)


class EmailSuppression(Base):
    """GDPR do-not-contact list — checked before every marketing send."""

    __tablename__ = "email_suppressions"
    __table_args__ = (Index("ix_email_suppressions_team_email", "team_id", "email", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False, default="unsubscribe")
    source: Mapped[str] = mapped_column(String(120), nullable=False, default="campaign")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
