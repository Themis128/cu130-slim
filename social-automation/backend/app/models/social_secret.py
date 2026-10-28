"""Social secrets model — account/service credentials and tokens.

Cloudflare-first, Postgres-failover, .env fallback is handled by the
secret_store service.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SocialSecret(Base):
    """Stores service and account credentials in the local Postgres DB.

    Acts as the failover for the Cloudflare D1-backed SSM. When D1 is
    unavailable, the secret_store service falls back to this table and
    finally to the local .env file.
    """

    __tablename__ = "social_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
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

    __table_args__ = (Index("ix_social_secrets_key", "key"),)
