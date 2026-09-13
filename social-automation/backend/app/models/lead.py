import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class LeadSource(enum.StrEnum):
    """Inbound channel that captured the lead (Meta organic messaging first)."""

    whatsapp_flow = "whatsapp_flow"
    whatsapp_dm = "whatsapp_dm"
    facebook_messenger = "facebook_messenger"
    instagram_dm = "instagram_dm"


class LeadInterest(enum.StrEnum):
    cloud = "cloud"
    growth = "growth"
    audit = "audit"


class LeadCompanySize(enum.StrEnum):
    solo = "solo"
    s_2_5 = "2-5"
    s_6_20 = "6-20"
    s_21_50 = "21-50"
    s_51_200 = "51-200"
    s_200_plus = "200+"


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_team_created", "team_id", "created_at"),
        Index("ix_leads_team_email", "team_id", "email"),
        Index("ix_leads_team_source", "team_id", "source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    source: Mapped[LeadSource] = mapped_column(
        SQLEnum(LeadSource, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        index=True,
    )
    social_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("social_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # The conversation identifier for the channel:
    # - Messenger: sender_psid
    # - Instagram: conversation_id
    # - WhatsApp: sender_phone or flow_token
    thread_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    company_size: Mapped[LeadCompanySize | None] = mapped_column(
        SQLEnum(LeadCompanySize, values_callable=lambda obj: [e.value for e in obj]),
        nullable=True,
    )
    interest: Mapped[LeadInterest | None] = mapped_column(
        SQLEnum(LeadInterest, values_callable=lambda obj: [e.value for e in obj]),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    meta_data: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    team: Mapped["Team"] = relationship("Team")
    social_account: Mapped["SocialAccount | None"] = relationship("SocialAccount")

