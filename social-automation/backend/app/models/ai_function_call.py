"""AI-function call audit — every DMR-backed function call lands a row:
fn, input hash, model, latency, outcome. Powers the auditable-outcomes
mandate and builds a fine-tune corpus over time."""

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:32]


class AIFunctionCall(Base):
    __tablename__ = "ai_function_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    fn: Mapped[str] = mapped_column(String(64), index=True)  # ai_classify, ai_extract, ...
    model: Mapped[str] = mapped_column(String(128), default="")
    input_sha: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(32), default="ok")  # ok | error | degraded
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
