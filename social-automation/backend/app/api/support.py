"""User support endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.deps import get_current_team
from app.db.session import get_db
from app.models.user import Team, User
from app.services.support_report import send_support_report

router = APIRouter()


class SupportReportIn(BaseModel):
    message: str = Field(..., min_length=10, max_length=4000)
    category: str = Field(default="general", max_length=50)
    reply_email: str | None = Field(default=None, max_length=255)


class SupportReportOut(BaseModel):
    ok: bool
    posted: bool
    error: str | None = None


@router.post("/report", response_model=SupportReportOut, status_code=status.HTTP_202_ACCEPTED)
async def report_issue(
    data: SupportReportIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    team: Team = Depends(get_current_team),
    db: AsyncSession = Depends(get_db),
) -> SupportReportOut:
    """Submit a support request with user/team diagnostics for troubleshooting."""
    reply_email = data.reply_email or current_user.email
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    report = await send_support_report(
        user=current_user,
        team=team,
        db=db,
        message=data.message,
        category=data.category,
        reply_email=reply_email,
        ip=ip,
        user_agent=user_agent,
    )
    return SupportReportOut(
        ok=report.get("ok", True),  # type: ignore[assignment]
        posted=report.get("posted", False),  # type: ignore[assignment]
        error=report.get("error"),  # type: ignore[assignment]
    )
