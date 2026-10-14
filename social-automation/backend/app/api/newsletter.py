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


# ── LinkedIn newsletter issue publishing ─────────────────────────────────────

_WORD_RE = re.compile(r"\S+")


def _word_count(html_or_text: str) -> int:
    return len(_WORD_RE.findall(re.sub(r"<[^>]+>", " ", html_or_text)))


def _article_html_from_generated(generated: dict) -> str:
    """Render the structured ``generate_linkedin_article`` output to the
    simple HTML the LinkedIn editor accepts (h2/p/ul/strong — same subset
    ``article_markdown_to_html`` emits)."""
    def esc(t: object) -> str:
        return html_mod.escape(str(t), quote=False)
    out: list[str] = []
    for section in generated.get("sections") or []:
        if section.get("heading"):
            out.append(f"<h2>{esc(section['heading'])}</h2>")
        if section.get("body"):
            out.append(f"<p>{esc(section['body'])}</p>")
    takeaways = [t for t in generated.get("takeaways") or [] if str(t).strip()]
    if takeaways:
        out.append("<h2>Key takeaways</h2>")
        out.append("<ul>" + "".join(f"<li>{esc(t)}</li>" for t in takeaways) + "</ul>")
    if generated.get("cta"):
        out.append(f"<p><strong>{esc(generated['cta'])}</strong></p>")
    return "\n".join(out)


class LinkedInIssueRequest(BaseModel):
    # Content sources — precedence: explicit > article_slug > topic
    article_slug: str | None = Field(default=None, max_length=200)
    topic: str | None = Field(default=None, max_length=300)
    title: str | None = Field(default=None, max_length=150)
    html_body: str | None = None
    text_body: str | None = None
    # Generation knobs (topic mode)
    sections: int = Field(default=9, ge=3, le=10)
    provider: str | None = None
    model: str | None = None
    # Metadata
    newsletter_name: str | None = Field(default="The Cloudless Build Log", max_length=150)
    seo_title: str | None = Field(default=None, max_length=220)
    seo_description: str | None = Field(default=None, max_length=400)
    post_description: str | None = Field(default=None, max_length=3000)
    schedule_iso: str | None = Field(default=None, max_length=40)
    cover_base64: str | None = None
    render_cover: bool = True
    issue_number: int | None = Field(default=None, ge=1)
    category: str | None = Field(default=None, max_length=64)
    min_words: int = Field(default=0, ge=0, le=20000)
    dry_run: bool = False


class LinkedInIssueResponse(BaseModel):
    status: str
    title: str
    word_count: int
    source: str
    dry_run: bool
    published: bool = False
    scheduled: bool = False
    article_url: str | None = None
    steps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@router.post("/linkedin/issue", response_model=LinkedInIssueResponse)
async def linkedin_issue(
    body: LinkedInIssueRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LinkedInIssueResponse:
    _require_service_token(request)

    warnings: list[str] = []
    title = (body.title or "").strip()
    html_body = (body.html_body or "").strip() or None
    text_body = (body.text_body or "").strip() or None
    seo_description = body.seo_description
    post_description = body.post_description
    category = body.category
    source = "explicit"

    if not (title and (html_body or text_body)):
        if body.article_slug:
            source = "article_slug"
            article = await blog_articles.get_published_article(body.article_slug)
            if not article:
                raise HTTPException(
                    status_code=404,
                    detail=f"No published article for slug {body.article_slug!r}",
                )
            title = title or (article.get("title") or "").strip()
            html_body = html_body or blog_articles.article_markdown_to_html(
                article.get("contentMarkdown") or ""
            )
            seo_description = seo_description or (article.get("excerpt") or None)
            post_description = post_description or blog_articles.clean_social_post(
                article.get("socialPost") or ""
            ) or None
            category = category or article.get("category")
        elif body.topic:
            source = "generated"
            kwargs: dict = {}
            if body.provider:
                kwargs["provider"] = body.provider
            if body.model:
                kwargs["model"] = body.model
            generated = await generate_linkedin_article(
                body.topic,
                sections=body.sections,
                include_takeaways=True,
                include_cta=True,
                db=db,
                **kwargs,
            )
            title = title or (generated.get("title") or "").strip()
            html_body = html_body or _article_html_from_generated(generated)
            seo_description = seo_description or (generated.get("subtitle") or None)
        else:
            raise HTTPException(
                status_code=422,
                detail="Provide title + html_body/text_body, an article_slug, or a topic",
            )

    if not title:
        raise HTTPException(status_code=422, detail="Resolved issue has no title")
    body_for_count = html_body or text_body or ""
    words = _word_count(body_for_count)
    if body.min_words and words < body.min_words:
        raise HTTPException(
            status_code=422,
            detail=f"Resolved body is {words} words — below min_words={body.min_words}",
        )
    if words < 800:
        warnings.append(
            f"Body is only {words} words — newsletter best practice is 1200+ (≥6 min read)"
        )

    cover_b64 = body.cover_base64
    if not cover_b64 and body.render_cover:
        png = render_newsletter_cover(
            title, issue_number=body.issue_number, category=category
        )
        cover_b64 = base64.b64encode(png).decode()

    client = LinkedInSidecarClient()
    try:
        result = await client.publish_newsletter_issue(
            title=title,
            html_body=html_body,
            text_body=None if html_body else text_body,
            cover_base64=cover_b64,
            cover_filename="cover.png",
            newsletter_name=body.newsletter_name,
            seo_title=body.seo_title or title,
            seo_description=(seo_description or "")[:300] or None,
            schedule_iso=body.schedule_iso,
            post_description=post_description,
            dry_run=body.dry_run,
        )
    except LinkedInSidecarError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc

    return LinkedInIssueResponse(
        status="ok",
        title=title,
        word_count=words,
        source=source,
        dry_run=body.dry_run,
        published=bool(result.get("published")),
        scheduled=bool(result.get("scheduled")),
        article_url=result.get("article_url"),
        steps=result.get("steps") or [],
        warnings=warnings,
    )
