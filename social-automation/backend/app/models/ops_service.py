"""Ops services — central registry for every manageable service in the fleet.

Sibling of ``ops_watchers``: watchers are scheduled jobs; services are
stateful things (containers, compose services, n8n workflows, systemd
units, Windows scheduled tasks, OMV units) that need status + on-demand
actions rather than cadence.

- ``executor="docker-cli"``  — agent runs docker/docker.exe commands
- ``executor="systemctl"``   — agent runs systemctl --user
- ``executor="windows-task"``— agent runs powershell.exe scheduled-task cmdlets
- ``executor="n8n-api"``     — observed via n8n REST (active/error)
- ``executor="external"``    — observed only (OMV cron/systemd, k3s)

``meta.allowed_actions`` gates what the agent may do (default
``["status"]``); ``restart``/``start``/``stop`` are only honored when
listed — critical infra (postgres, redis, n8n, tunnels, browsers) is
seeded with ``["status","logs"]`` so the platform can see but not
bounce them without an explicit policy change.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ServiceTier(enum.StrEnum):
    DOCKER = "docker"  # containers / compose services
    N8N = "n8n"  # workflows
    SYSTEMD = "systemd"  # WSL user units
    WINDOWS = "windows"  # Task Scheduler
    INFRA = "infra"  # OMV, k3s — observed only
    EXTERNAL = "external"


class ServiceKind(enum.StrEnum):
    CONTAINER = "container"
    WORKFLOW = "workflow"
    SERVICE = "service"
    TASK = "task"


class ServiceExecutor(enum.StrEnum):
    DOCKER_CLI = "docker-cli"
    SYSTEMCTL = "systemctl"
    WINDOWS_TASK = "windows-task"
    N8N_API = "n8n-api"
    EXTERNAL = "external"


class ServiceManagedBy(enum.StrEnum):
    PLATFORM = "platform"
    EXTERNAL = "external"


class OpsService(Base):
    __tablename__ = "ops_services"
    __table_args__ = (Index("ix_ops_services_status", "enabled", "managed_by", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    tier: Mapped[ServiceTier] = mapped_column(SQLEnum(ServiceTier, values_callable=lambda o: [e.value for e in o]), nullable=False)
    kind: Mapped[ServiceKind] = mapped_column(
        SQLEnum(ServiceKind, values_callable=lambda o: [e.value for e in o]),
        nullable=False,
        default=ServiceKind.SERVICE,
    )
    executor: Mapped[ServiceExecutor] = mapped_column(
        SQLEnum(ServiceExecutor, values_callable=lambda o: [e.value for e in o]),
        nullable=False,
    )
    managed_by: Mapped[ServiceManagedBy] = mapped_column(
        SQLEnum(ServiceManagedBy, values_callable=lambda o: [e.value for e in o]),
        nullable=False,
        default=ServiceManagedBy.EXTERNAL,
    )
    # docker container name / systemd unit / task name / n8n workflow id
    target: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    health_url: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # observed state — written by agent reports
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    # running | stopped | restarting | unhealthy | unknown | disabled
    health: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reported_by: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    last_action: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # pending action claimed by agent
    pending_action: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    pending_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
