"""Ops watchers — central registry for every watchdog/auto-healer in the fleet.

One row per watcher regardless of where it executes:

- ``executor="celery"``    — a Celery beat task; gated centrally by the
  ``task_prerun`` enabled-check in ``app/worker/watchdog_gate.py``.
- ``executor="host-agent"``— a shell command run by ``scripts/watcher_agent.py``
  on the WSL/Windows host (docker.exe, powershell.exe, adb, cron scripts).
- ``executor="external"``  — owned outside the platform (OMV cron, n8n
  schedule, Windows Task Scheduler); the agent reports freshness only.

``managed_by`` is the migration state: ``external`` rows are observed only;
``platform`` rows are due-scheduled by ``app.worker.tasks.watcher_scheduler``
and executed by the matching executor. ``enabled=False`` pauses platform
watchers (celery gate skips the task; agent skips host commands). External
watchers cannot be stopped by the platform — the flag is advisory and shown
as such in the UI.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WatcherTier(enum.StrEnum):
    PLATFORM = "platform"  # celery beat tasks
    HOST = "host"  # WSL crontab / heal_supervisor / stack watchers
    WINDOWS = "windows"  # Windows Task Scheduler (reached via host agent)
    DOCKER = "docker"  # autoheal / compose-level
    INFRA = "infra"  # OMV, k3s, n8n — observed only
    EXTERNAL = "external"  # anything else observed only


class WatcherKind(enum.StrEnum):
    WATCH = "watch"
    HEAL = "heal"
    SYNC = "sync"
    REPORT = "report"
    JANITOR = "janitor"


class WatcherExecutor(enum.StrEnum):
    CELERY = "celery"
    HOST_AGENT = "host-agent"
    EXTERNAL = "external"


class WatcherManagedBy(enum.StrEnum):
    PLATFORM = "platform"
    EXTERNAL = "external"


class OpsWatcher(Base):
    __tablename__ = "ops_watchers"
    __table_args__ = (
        Index("ix_ops_watchers_due", "enabled", "managed_by", "executor", "next_due_at"),
        Index("ix_ops_watchers_last_run", "last_finished_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    tier: Mapped[WatcherTier] = mapped_column(SQLEnum(WatcherTier, values_callable=lambda o: [e.value for e in o]), nullable=False)
    kind: Mapped[WatcherKind] = mapped_column(SQLEnum(WatcherKind, values_callable=lambda o: [e.value for e in o]), nullable=False, default=WatcherKind.WATCH)
    executor: Mapped[WatcherExecutor] = mapped_column(SQLEnum(WatcherExecutor, values_callable=lambda o: [e.value for e in o]), nullable=False)
    managed_by: Mapped[WatcherManagedBy] = mapped_column(
        SQLEnum(WatcherManagedBy, values_callable=lambda o: [e.value for e in o]), nullable=False, default=WatcherManagedBy.EXTERNAL
    )
    # celery task path (executor=celery) or shell command (executor=host-agent)
    command: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # cron expression for display; interval_seconds is authoritative for host-agent
    schedule_cron: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    # extra context: log path, alert route, source file, notes — never secrets
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # runtime state — written by scheduler/agent/gate
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    # ok | running | error | disabled | stale | unknown
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_output_tail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
