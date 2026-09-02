"""AI usage tracking model.

Tracks which provider/model was called, how long it took, and whether it
succeeded. Designed for cost/Neuron observability and Metabase dashboards.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIUsageLog(Base):
    __tablename__ = "ai_usage_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    endpoint: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    prompt_length: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_neurons: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_neurons: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_data: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
