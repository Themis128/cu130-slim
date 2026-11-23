"""Ops watchers — central registry + control plane for every watchdog/healer.

Management endpoints (UI, user JWT):
  GET  /ops/watchers                 list all watchers + live status
  POST /ops/watchers/{name}/toggle   {enabled: bool}
  POST /ops/watchers/{name}/run      force run-now (celery→send_task, host-agent→due now)
  POST /ops/watchers/sync            upsert inventory rows (idempotent seed)

Agent endpoints (host watcher-agent, X-Service-Token):
  GET  /ops/watchers/due             due host-agent watchers + external freshness asks
  POST /ops/watchers/{name}/result   run/heartbeat result
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models.ops_watcher import (
    OpsWatcher,
    WatcherExecutor,
    WatcherKind,
    WatcherManagedBy,
    WatcherTier,
)
from app.models.user import User

router = APIRouter()


def _require_agent_token(request: Request) -> None:
    settings = get_settings()
    expected = (getattr(settings, "OPS_WATCHER_AGENT_TOKEN", "") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="OPS_WATCHER_AGENT_TOKEN not configured")
    provided = (request.headers.get("X-Service-Token") or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid service token")


def _out(w: OpsWatcher) -> dict[str, Any]:
    now = datetime.now(UTC)
    stale = False
    if w.managed_by.value == "platform" and w.last_finished_at:
        horizon = max(w.interval_seconds * 4, 3600)
        stale = (now - w.last_finished_at) > timedelta(seconds=horizon)
    status = w.status
    if w.enabled is False:
        status = "disabled"
    elif stale and status == "ok":
        status = "stale"
    return {
        "id": str(w.id),
        "name": w.name,
        "title": w.title,
        "tier": w.tier.value,
        "kind": w.kind.value,
        "executor": w.executor.value,
        "managed_by": w.managed_by.value,
        "command": w.command,
        "schedule_cron": w.schedule_cron,
        "interval_seconds": w.interval_seconds,
        "enabled": w.enabled,
        "timeout_seconds": w.timeout_seconds,
        "meta": w.meta or {},
        "status": status,
        "last_started_at": w.last_started_at.isoformat() if w.last_started_at else None,
        "last_finished_at": w.last_finished_at.isoformat() if w.last_finished_at else None,
        "last_exit_code": w.last_exit_code,
        "last_output_tail": (w.last_output_tail or "")[-500:],
        "consecutive_failures": w.consecutive_failures,
        "next_due_at": w.next_due_at.isoformat() if w.next_due_at else None,
    }


@router.get("/watchers")
async def list_watchers(
    tier: str | None = None,
    enabled: bool | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _ = current_user
    q = select(OpsWatcher).order_by(OpsWatcher.tier, OpsWatcher.name)
    rows = (await db.execute(q)).scalars().all()
    out = [_out(w) for w in rows]
    if tier:
        out = [w for w in out if w["tier"] == tier]
    if enabled is not None:
        out = [w for w in out if w["enabled"] == enabled]
    return {"watchers": out, "count": len(out)}


class ToggleBody(BaseModel):
    enabled: bool


@router.post("/watchers/{name}/toggle")
async def toggle_watcher(
    name: str,
    body: ToggleBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _ = current_user
    w = (await db.execute(select(OpsWatcher).where(OpsWatcher.name == name))).scalar_one_or_none()
    if not w:
        raise HTTPException(404, "watcher not found")
    w.enabled = body.enabled
    w.status = "ok" if body.enabled else "disabled"
    if body.enabled and w.managed_by == WatcherManagedBy.PLATFORM and w.next_due_at is None:
        w.next_due_at = datetime.now(UTC)
    await db.commit()
    return _out(w)


@router.post("/watchers/{name}/run")
async def run_watcher(
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _ = current_user
    w = (await db.execute(select(OpsWatcher).where(OpsWatcher.name == name))).scalar_one_or_none()
    if not w:
        raise HTTPException(404, "watcher not found")
    if w.executor == WatcherExecutor.CELERY and w.command:
        from app.worker.celery_app import celery_app

        celery_app.send_task(w.command)
        w.last_started_at = datetime.now(UTC)
        w.status = "running"
        await db.commit()
        return {**_out(w), "dispatched": "celery"}
    if w.executor == WatcherExecutor.HOST_AGENT:
        w.next_due_at = datetime.now(UTC)
        await db.commit()
        return {**_out(w), "dispatched": "host-agent"}
    raise HTTPException(400, f"watcher executor '{w.executor.value}' cannot be run on demand")


class SyncItem(BaseModel):
    name: str
    title: str = ""
    tier: str
    kind: str = "watch"
    executor: str
    managed_by: str = "external"
    command: str = ""
    schedule_cron: str = ""
    interval_seconds: int = 300
    timeout_seconds: int = 300
    enabled: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)


class SyncBody(BaseModel):
    watchers: list[SyncItem]


@router.post("/watchers/sync")
async def sync_watchers(
    body: SyncBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Idempotent upsert of inventory rows — service-token or user JWT."""
    token = (request.headers.get("X-Service-Token") or "").strip()
    expected = (getattr(get_settings(), "OPS_WATCHER_AGENT_TOKEN", "") or "").strip()
    if not (token and expected and secrets.compare_digest(token, expected)):
        # fall back to normal user auth by re-checking via dependency is complex;
        # require the service token for machine callers — UI uses /watchers UI paths.
        raise HTTPException(401, "sync requires X-Service-Token")
    created = updated = 0

    def _en(cls, val: str):
        try:
            return cls[val.upper()]
        except KeyError:
            return cls(val)

    for it in body.watchers:
        w = (await db.execute(select(OpsWatcher).where(OpsWatcher.name == it.name))).scalar_one_or_none()
        if w is None:
            w = OpsWatcher(name=it.name)
            db.add(w)
            created += 1
        w.title = it.title or it.name
        w.tier = _en(WatcherTier, it.tier)
        w.kind = _en(WatcherKind, it.kind)
        w.executor = _en(WatcherExecutor, it.executor)
        w.managed_by = _en(WatcherManagedBy, it.managed_by)
        w.command = it.command
        w.schedule_cron = it.schedule_cron
        w.interval_seconds = it.interval_seconds
        w.timeout_seconds = it.timeout_seconds
        w.meta = it.meta
        w.enabled = it.enabled
        if w.next_due_at is None and w.managed_by == WatcherManagedBy.PLATFORM:
            w.next_due_at = datetime.now(UTC)
        updated += 1
    await db.commit()
    return {"created": created, "upserted": updated}


# ---------- agent-facing ----------


@router.get("/watchers/due")
async def due_watchers(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    _require_agent_token(request)
    now = datetime.now(UTC)
    rows = (
        (
            await db.execute(
                select(OpsWatcher).where(
                    OpsWatcher.managed_by == WatcherManagedBy.PLATFORM,
                    OpsWatcher.executor == WatcherExecutor.HOST_AGENT,
                    OpsWatcher.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    due = [w for w in rows if w.next_due_at is None or w.next_due_at <= now]
    obs = (
        (
            await db.execute(
                select(OpsWatcher).where(
                    OpsWatcher.managed_by == WatcherManagedBy.EXTERNAL,
                    OpsWatcher.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    # claim: push next_due_at forward so a second agent can't double-run
    for w in due:
        w.next_due_at = now + timedelta(seconds=w.interval_seconds)
        w.status = "running"
        w.last_started_at = now
    await db.commit()
    return {"due": [_out(w) for w in due], "observe": [_out(w) for w in obs], "ts": now.isoformat()}


class ResultBody(BaseModel):
    exit_code: int = 0
    output_tail: str = ""
    duration_seconds: float = 0.0
    observed: bool = False  # True = heartbeat/freshness report, not an execution


@router.post("/watchers/{name}/result")
async def watcher_result(
    name: str,
    body: ResultBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _require_agent_token(request)
    w = (await db.execute(select(OpsWatcher).where(OpsWatcher.name == name))).scalar_one_or_none()
    if not w:
        raise HTTPException(404, "watcher not found")
    now = datetime.now(UTC)
    if body.observed:
        w.status = "ok" if body.exit_code == 0 else "stale"
        w.last_finished_at = now
        w.last_exit_code = body.exit_code
        if body.output_tail:
            w.last_output_tail = body.output_tail[-2000:]
    else:
        w.last_finished_at = now
        w.last_exit_code = body.exit_code
        w.last_output_tail = (body.output_tail or "")[-2000:]
        w.consecutive_failures = 0 if body.exit_code == 0 else w.consecutive_failures + 1
        w.status = "ok" if body.exit_code == 0 else "error"
        if w.managed_by == WatcherManagedBy.PLATFORM:
            w.next_due_at = now + timedelta(seconds=w.interval_seconds)
    await db.commit()
    return {"ok": True, "status": w.status}
