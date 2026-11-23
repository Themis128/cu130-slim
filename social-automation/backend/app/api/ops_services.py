"""Ops services — central registry + control plane for fleet services.

Management (user JWT):
  GET  /ops/services                 list all services + live status
  POST /ops/services/{name}/action   {action: restart|start|stop|logs|status}

Agent (X-Service-Token):
  POST /ops/services/sync            inventory upsert
  GET  /ops/services/actions         pending actions to execute
  POST /ops/services/report          batch status/health reports
  POST /ops/services/{name}/action-result  action outcome
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models.ops_service import OpsService
from app.models.user import User

router = APIRouter()

ACTIONS = {"restart", "start", "stop", "logs", "status"}


def _require_agent_token(request: Request) -> None:
    settings = get_settings()
    expected = (getattr(settings, "OPS_WATCHER_AGENT_TOKEN", "") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="OPS_WATCHER_AGENT_TOKEN not configured")
    provided = (request.headers.get("X-Service-Token") or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid service token")


def _allowed(s: OpsService, action: str) -> bool:
    return action in (s.meta or {}).get("allowed_actions", ["status"])


def _out(s: OpsService) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "name": s.name,
        "title": s.title,
        "tier": s.tier.value,
        "kind": s.kind.value,
        "executor": s.executor.value,
        "managed_by": s.managed_by.value,
        "target": s.target,
        "health_url": s.health_url,
        "meta": s.meta or {},
        "enabled": s.enabled,
        "status": "disabled" if not s.enabled else s.status,
        "health": s.health,
        "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
        "last_reported_by": s.last_reported_by,
        "last_action": s.last_action,
        "last_action_at": s.last_action_at.isoformat() if s.last_action_at else None,
        "pending_action": s.pending_action or None,
        "consecutive_failures": s.consecutive_failures,
    }


@router.get("/services")
async def list_services(
    tier: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _ = current_user
    rows = (await db.execute(select(OpsService).order_by(OpsService.tier, OpsService.name))).scalars().all()
    out = [_out(s) for s in rows]
    if tier:
        out = [s for s in out if s["tier"] == tier]
    if status:
        out = [s for s in out if s["status"] == status]
    return {"services": out, "count": len(out)}


class ActionBody(BaseModel):
    action: str
    reason: str = ""


@router.post("/services/{name}/action")
async def service_action(
    name: str,
    body: ActionBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _ = current_user
    if body.action not in ACTIONS:
        raise HTTPException(400, f"action must be one of {sorted(ACTIONS)}")
    s = (await db.execute(select(OpsService).where(OpsService.name == name))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "service not found")
    if not s.enabled:
        raise HTTPException(409, "service disabled in registry")
    if not _allowed(s, body.action):
        raise HTTPException(403, f"action '{body.action}' not in allowed_actions for {name}")
    s.pending_action = body.action
    s.pending_action_at = datetime.now(UTC)
    await db.commit()
    return {**_out(s), "queued": body.action}


# ---------- agent-facing ----------


class SyncItem(BaseModel):
    name: str
    title: str = ""
    tier: str
    kind: str = "service"
    executor: str
    managed_by: str = "external"
    target: str = ""
    health_url: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class SyncBody(BaseModel):
    services: list[SyncItem]


@router.post("/services/sync")
async def sync_services(body: SyncBody, request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    _require_agent_token(request)
    from app.models.ops_service import (
        ServiceExecutor,
        ServiceKind,
        ServiceManagedBy,
        ServiceTier,
    )

    def _en(cls, val: str):
        try:
            return cls[val.upper()]
        except KeyError:
            return cls(val)

    created = updated = 0
    for it in body.services:
        s = (await db.execute(select(OpsService).where(OpsService.name == it.name))).scalar_one_or_none()
        if s is None:
            s = OpsService(name=it.name)
            db.add(s)
            created += 1
        s.title = it.title or it.name
        s.tier = _en(ServiceTier, it.tier)
        s.kind = _en(ServiceKind, it.kind)
        s.executor = _en(ServiceExecutor, it.executor)
        s.managed_by = _en(ServiceManagedBy, it.managed_by)
        s.target = it.target
        s.health_url = it.health_url
        # merge meta — never clobber allowed_actions set by policy
        merged = dict(s.meta or {})
        merged.update(it.meta or {})
        s.meta = merged
        s.enabled = it.enabled
        updated += 1
    await db.commit()
    return {"created": created, "upserted": updated}


@router.get("/services/actions")
async def pending_actions(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    _require_agent_token(request)
    rows = (await db.execute(select(OpsService).where(OpsService.pending_action != ""))).scalars().all()
    return {"actions": [_out(s) for s in rows]}


class ReportItem(BaseModel):
    name: str
    status: str = "unknown"
    health: str = "unknown"
    detail: str = ""


class ReportBody(BaseModel):
    reports: list[ReportItem]
    reported_by: str = "watcher-agent"


@router.post("/services/report")
async def service_report(body: ReportBody, request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    _require_agent_token(request)
    now = datetime.now(UTC)
    applied = 0
    for r in body.reports:
        s = (await db.execute(select(OpsService).where(OpsService.name == r.name))).scalar_one_or_none()
        if not s:
            continue
        s.status = r.status[:40]
        s.health = r.health[:40]
        s.last_seen_at = now
        s.last_reported_by = body.reported_by[:60]
        if r.detail:
            s.meta = {**(s.meta or {}), "last_detail": r.detail[-500:]}
        applied += 1
    await db.commit()
    return {"applied": applied}


class ActionResultBody(BaseModel):
    ok: bool
    output_tail: str = ""


@router.post("/services/{name}/action-result")
async def action_result(
    name: str,
    body: ActionResultBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _require_agent_token(request)
    s = (await db.execute(select(OpsService).where(OpsService.name == name))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "service not found")
    now = datetime.now(UTC)
    s.last_action = f"{s.pending_action or '?'} {'ok' if body.ok else 'FAIL'}"
    s.last_action_at = now
    s.pending_action = ""
    s.pending_action_at = None
    s.consecutive_failures = 0 if body.ok else s.consecutive_failures + 1
    if body.output_tail:
        s.meta = {**(s.meta or {}), "last_action_output": body.output_tail[-1000:]}
    await db.commit()
    return {"ok": True}
