"""Audit log API — list and filter audit entries."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.db.session import get_db
from app.models.user import AuditLog, TeamMember, User

router = APIRouter()


class AuditLogOut(BaseModel):
    id: uuid.UUID
    user_email: str | None
    action: str
    resource_type: str
    resource_id: str | None
    detail: str | None
    ip_address: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogListResponse(BaseModel):
    entries: list[AuditLogOut]
    total: int
    page: int
    page_size: int


@router.get("/audit-logs", response_model=AuditLogListResponse)
async def list_audit_logs(
    action: str | None = None,
    resource_type: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List audit log entries (admin/owner only)."""
    result = await db.execute(
        select(TeamMember.team_id).where(TeamMember.user_id == current_user.id)
    )
    team_id = result.scalar_one_or_none()
    if not team_id:
        raise HTTPException(status_code=404, detail="Team not found")

    q = select(AuditLog).where(AuditLog.team_id == team_id)
    count_q = select(AuditLog).where(AuditLog.team_id == team_id)

    if action:
        q = q.where(AuditLog.action == action)
        count_q = count_q.where(AuditLog.action == action)
    if resource_type:
        q = q.where(AuditLog.resource_type == resource_type)
        count_q = count_q.where(AuditLog.resource_type == resource_type)
    if start_date:
        q = q.where(AuditLog.created_at >= start_date)
        count_q = count_q.where(AuditLog.created_at >= start_date)
    if end_date:
        q = q.where(AuditLog.created_at <= end_date)
        count_q = count_q.where(AuditLog.created_at <= end_date)

    from sqlalchemy import func
    total_result = await db.execute(select(func.count()).select_from(count_q.subquery()))
    total = total_result.scalar() or 0

    q = q.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    entries = result.scalars().all()

    return AuditLogListResponse(
        entries=[AuditLogOut.model_validate(e) for e in entries],
        total=total,
        page=page,
        page_size=page_size,
    )
