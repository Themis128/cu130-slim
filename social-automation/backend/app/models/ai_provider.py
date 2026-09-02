import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import BYTEA, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AIProvider(Base):
    __tablename__ = "ai_providers"
    __table_args__ = (
        UniqueConstraint("team_id", "name", name="uq_ai_provider_team_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)           # nvidia | huggingface | openai | groq | together | ollama
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    api_key_enc: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)  # null for local ollama
    base_url: Mapped[str] = mapped_column(String(300), nullable=False)
    default_model: Mapped[str] = mapped_column(String(200), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # ── Routing policy (Phase 4) ──
    fallbacks: Mapped[str | None] = mapped_column(String(500), nullable=True)  # comma-separated provider names
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    daily_neuron_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ── Circuit breaker state (Phase 4) ──
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    circuit_open: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    team: Mapped["Team"] = relationship("Team")
