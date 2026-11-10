"""Ops incidents — the shared incident object for the AgentOps loop.

Detectors (host watchdogs, compose-healer, session healers, ad-hoc scripts)
report into ``ops_incidents`` via ``scripts/ops_incident.py`` or the service
layer. The ``ops-agent`` Celery sweep triages open incidents: gathers evidence,
runs an LLM root-cause pass, applies the safety policy, executes whitelisted
actions, verifies resolution and escalates to the severity-routed Telegram
channels. See ``app/services/ops_agent.py``.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IncidentStatus(enum.StrEnum):
    OPEN = "open"
    TRIAGED = "triaged"
    ACTIONED = "actioned"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class IncidentSeverity(enum.StrEnum):
    INFO = "info"
    OPS = "ops"
    CRITICAL = "critical"


class OpsIncident(Base):
    __tablename__ = "ops_incidents"
    __table_args__ = (
        # fingerprint is UNIQUE — report_incident upserts ON CONFLICT, and the
        # host-side psql fallback (scripts/ops_incident_host.sh) relies on it.
        Index("ix_ops_incidents_status_fingerprint", "status", "fingerprint"),
        Index("ix_ops_incidents_status_last_seen", "status", "last_seen"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # sha256(source|kind|target)[:32] — dedup key; recurrence after resolve reopens.
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    # Free-form target: container name, host, URL, account id, ...
    target: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    severity: Mapped[IncidentSeverity] = mapped_column(
        SQLEnum(IncidentSeverity, values_callable=lambda obj: [e.value for e in obj]),
        default=IncidentSeverity.OPS,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(
        SQLEnum(IncidentStatus, values_callable=lambda obj: [e.value for e in obj]),
        default=IncidentStatus.OPEN,
        nullable=False,
        index=True,
    )
    # Agent outputs
    rca: Mapped[str] = mapped_column(Text, default="", nullable=False)
    proposed_action: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    action_taken: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    action_result: Mapped[str] = mapped_column(Text, default="", nullable=False)
    agent_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    recurrence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)
