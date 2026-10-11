"""Newsletter cover endpoints — server-to-server generation for cloudless.gr.

The cloudless.gr weekly newsletter cron calls POST /newsletter/cover before
assembling the issue; SocialAuto renders the branded banner and returns the
PNG bytes. Auth: X-Service-Token must match SOCIALAUTO_SERVICE_TOKEN.
"""

from __future__ import annotations

import secrets
from datetime import date

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.services.newsletter_cover import default_issue_date, render_newsletter_cover

# The API router mounts this module under `/newsletter` via `include_router(..., prefix="/newsletter")`.
router = APIRouter(tags=["newsletter"])


class NewsletterCoverRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    issue_number: int | None = Field(default=None, ge=1)
    issue_date: str | None = Field(default=None, max_length=32)
    category: str | None = Field(default=None, max_length=64)


def _require_service_token(request: Request) -> None:
    settings = get_settings()
    expected = (settings.NEWSLETTER_SERVICE_TOKEN or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="NEWSLETTER_SERVICE_TOKEN is not configured",
        )
    provided = (request.headers.get("X-Service-Token") or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid service token")


@router.post("/cover")
async def newsletter_cover(body: NewsletterCoverRequest, request: Request) -> Response:
    _require_service_token(request)
    issue_date = (body.issue_date or "").strip() or default_issue_date()
    try:
        date.fromisoformat(issue_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid issue_date: {exc}") from exc
    png = render_newsletter_cover(
        body.title,
        issue_number=body.issue_number,
        issue_date=issue_date,
        category=body.category,
    )
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )
