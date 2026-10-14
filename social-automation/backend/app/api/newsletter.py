"""Newsletter endpoints — covers + LinkedIn issue publishing.

The cloudless.gr weekly newsletter cron calls POST /newsletter/cover before
assembling the issue; SocialAuto renders the branded banner and returns the
PNG bytes. Auth: X-Service-Token must match NEWSLETTER_SERVICE_TOKEN.

POST /newsletter/linkedin/issue publishes (or dry-runs) a LinkedIn
newsletter edition. LinkedIn exposes no newsletter API, so the request is
forwarded to the linkedin-browser-sidecar which drives the article editor.
Content resolves three ways, in precedence order:

1. explicit ``title`` + ``html_body``/``text_body`` — caller supplies all,
2. ``article_slug`` — reuses the published cloudless.gr blog article from
   the R2 datalake (republishing the weekly blog article as the LinkedIn
   edition is the documented best practice),
3. ``topic`` — generates a fresh long-form article via
   ``generate_linkedin_article`` (DMR + plain-English rewrite).
"""

from __future__ import annotations

import base64
import html as html_mod
import re
import secrets
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.services import blog_articles
from app.services.linkedin_ai import generate_linkedin_article
from app.services.linkedin_sidecar import LinkedInSidecarClient, LinkedInSidecarError
from app.services.newsletter_cover import default_issue_date, render_newsletter_cover

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
