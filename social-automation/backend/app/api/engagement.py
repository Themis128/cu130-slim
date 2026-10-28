"""Human-gate API — pending engagement approvals for the admin feed."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.engagement import EngagementAction
from app.models.user import User

router = APIRouter(prefix="/engagement", tags=["engagement"])


class GateItem(BaseModel):
    id: str
    platform: str
    target_author: str
    action: str
    score: float
    comment_draft: str | None
    status: str


class GateUpdate(BaseModel):
    comment_draft: str | None = None


@router.get("/pending", response_model=list[GateItem])
async def pending(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (
        (await db.execute(select(EngagementAction).where(EngagementAction.status == "pending_human_gate").order_by(EngagementAction.score.desc()).limit(50)))
        .scalars()
        .all()
    )
    return [
        GateItem(
            id=str(r.id), platform=r.platform, target_author=r.target_author, action=r.action, score=r.score, comment_draft=r.comment_draft, status=r.status
        )
        for r in rows
    ]


@router.post("/{action_id}/approve", response_model=GateItem)
async def approve(action_id: str, body: GateUpdate | None = None, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    import uuid as _u

    r = (await db.execute(select(EngagementAction).where(EngagementAction.id == _u.UUID(action_id)))).scalars().first()
    if not r:
        raise HTTPException(404, "not found")
    if body and body.comment_draft:
        r.comment_draft = body.comment_draft
    r.status = "pending"  # re-enters dispatch → actor executes
    await db.commit()
    return GateItem(
        id=str(r.id), platform=r.platform, target_author=r.target_author, action=r.action, score=r.score, comment_draft=r.comment_draft, status=r.status
    )


@router.post("/{action_id}/reject", response_model=GateItem)
async def reject(action_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    import uuid as _u

    r = (await db.execute(select(EngagementAction).where(EngagementAction.id == _u.UUID(action_id)))).scalars().first()
    if not r:
        raise HTTPException(404, "not found")
    r.status = "rejected"
    await db.commit()
    return GateItem(
        id=str(r.id), platform=r.platform, target_author=r.target_author, action=r.action, score=r.score, comment_draft=r.comment_draft, status=r.status
    )
