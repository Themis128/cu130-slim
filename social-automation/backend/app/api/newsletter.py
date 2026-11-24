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
import uuid
from datetime import UTC, date, datetime
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.services import blog_articles, newsletter_issue_queue, newsletter_publisher
from app.services.browser_bridge import BrowserBridgeError
from app.services.linkedin_ai import generate_linkedin_article
from app.services.linkedin_newsletter_bridge import (
    LinkedInNewsletterBridgeError,
)
from app.services.linkedin_sidecar import LinkedInSidecarError
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
    # Title is printed on produced media — run it through LanguageTool +
    # PIL-safe preprocessing like every other rendered copy path.
    from app.services.spellcheck import auto_correct, preprocess_for_render

    try:
        title = preprocess_for_render(await auto_correct(body.title)) or body.title
    except Exception:  # noqa: BLE001 — correction must never block the cover
        title = body.title
    png = render_newsletter_cover(
        title,
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
    # `[^<>]*` (not `[^>]+`) keeps tag-stripping linear on `<`-heavy input —
    # CodeQL py/polynomial-redos.
    return len(_WORD_RE.findall(re.sub(r"<[^<>]*>", " ", html_or_text)))


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
    # Inline media appended at the end of the article body (sidecar's
    # "Add image or video" dialog): [{type:"image"|"video", asset_id?}|
    # {type, base64, filename?}|{type, url, filename?}]. Queued issues keep
    # the spec in result["_media"]; asset_id entries resolve at dispatch.
    media: list[dict] | None = None
    dry_run: bool = False
    # Browser path: the dedicated sidecar is primary; on failure "auto"
    # falls back to the shared browser-novnc bridge session (which holds a
    # long-lived LinkedIn login and hydrates the editor reliably).
    browser: Literal["auto", "sidecar", "bridge"] = "auto"


class LinkedInIssueResponse(BaseModel):
    status: str
    title: str
    word_count: int
    source: str
    dry_run: bool
    browser: str = "sidecar"
    published: bool = False
    scheduled: bool = False
    article_url: str | None = None
    steps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class _ResolvedIssue(BaseModel):
    """Content resolved from explicit fields / article_slug / topic."""

    title: str
    html_body: str | None = None
    text_body: str | None = None
    seo_description: str | None = None
    post_description: str | None = None
    category: str | None = None
    warnings: list[str] = Field(default_factory=list)
    words: int = 0
    source: str = "explicit"


async def _resolve_issue_content(body: LinkedInIssueRequest, db: AsyncSession) -> _ResolvedIssue:
    """Resolve title + body from the three content sources, in precedence
    order: explicit > article_slug > topic. Shared by the synchronous
    ``/linkedin/issue`` publish and the queued ``/issues`` enqueue."""
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
            html_body = (
                html_body
                or (article.get("html") or "").strip()
                or blog_articles.article_markdown_to_html(article.get("contentMarkdown") or article.get("content") or "")
            )
            seo_description = seo_description or (article.get("excerpt") or None)
            post_description = post_description or blog_articles.clean_social_post(article.get("socialPost") or "") or None
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
        warnings.append(f"Body is only {words} words — newsletter best practice is 1200+ (≥6 min read)")

    return _ResolvedIssue(
        title=title,
        html_body=html_body,
        text_body=text_body,
        seo_description=seo_description,
        post_description=post_description,
        category=category,
        warnings=warnings,
        words=words,
        source=source,
    )


@router.post("/linkedin/issue", response_model=LinkedInIssueResponse)
async def linkedin_issue(
    body: LinkedInIssueRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LinkedInIssueResponse:
    _require_service_token(request)

    resolved = await _resolve_issue_content(body, db)

    cover_b64 = body.cover_base64
    if not cover_b64 and body.render_cover:
        png = render_newsletter_cover(resolved.title, issue_number=body.issue_number, category=resolved.category)
        cover_b64 = base64.b64encode(png).decode()

    try:
        result, via, publish_warnings = await newsletter_publisher.publish_with_fallback(
            title=resolved.title,
            html_body=resolved.html_body,
            text_body=resolved.text_body,
            cover_base64=cover_b64,
            cover_filename="cover.png",
            newsletter_name=body.newsletter_name,
            seo_title=body.seo_title or resolved.title,
            seo_description=(resolved.seo_description or "")[:300] or None,
            schedule_iso=body.schedule_iso,
            post_description=resolved.post_description,
            media=await newsletter_publisher.resolve_media_items(db, body.media),
            dry_run=body.dry_run,
            browser=body.browser,
        )
    except LinkedInSidecarError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    except (LinkedInNewsletterBridgeError, BrowserBridgeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return LinkedInIssueResponse(
        status="ok",
        title=resolved.title,
        word_count=resolved.words,
        source=resolved.source,
        dry_run=body.dry_run,
        browser=via,
        published=bool(result.get("published")),
        scheduled=bool(result.get("scheduled")),
        article_url=result.get("article_url"),
        steps=result.get("steps") or [],
        warnings=resolved.warnings + publish_warnings,
    )


# ── queued issues (platform-owned newsletter scheduler) ──────────────────────


class NewsletterIssueEnqueueRequest(LinkedInIssueRequest):
    """``/linkedin/issue`` payload + scheduling/dedup fields."""

    scheduled_for: str | None = Field(default=None, max_length=40)
    slug: str | None = Field(default=None, max_length=220)
    dispatch_now: bool = False


class NewsletterIssueOut(BaseModel):
    id: str
    slug: str
    title: str
    status: str
    scheduled_for: str | None = None
    article_url: str | None = None
    published_at: str | None = None
    verified_at: str | None = None
    attempts: int = 0
    verify_count: int = 0
    last_error: str = ""
    dry_run: bool = False
    result: dict = Field(default_factory=dict)


def _issue_out(issue, *, created: bool | None = None) -> dict:
    out = NewsletterIssueOut(
        id=str(issue.id),
        slug=issue.slug,
        title=issue.title,
        status=issue.status.value if hasattr(issue.status, "value") else str(issue.status),
        scheduled_for=issue.scheduled_for.isoformat() if issue.scheduled_for else None,
        article_url=issue.article_url,
        published_at=issue.published_at.isoformat() if issue.published_at else None,
        verified_at=issue.verified_at.isoformat() if issue.verified_at else None,
        attempts=issue.attempts,
        verify_count=issue.verify_count,
        last_error=issue.last_error or "",
        dry_run=issue.dry_run,
        result=issue.result or {},
    )
    data = out.model_dump()
    if created is not None:
        data["created"] = created
    return data


def _parse_issue_uuid(issue_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(issue_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid issue id") from exc


def _parse_scheduled_for(raw: str | None):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid scheduled_for: {exc}") from exc


@router.post("/issues")
async def enqueue_newsletter_issue(
    body: NewsletterIssueEnqueueRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Enqueue a LinkedIn newsletter issue for the dispatch sweep.

    Content resolves exactly like ``/linkedin/issue`` (explicit >
    article_slug > topic) but nothing is published here — the
    ``newsletter-issue-dispatch`` beat claims the row when
    ``scheduled_for`` arrives. Idempotent on ``slug``.
    """
    _require_service_token(request)
    resolved = await _resolve_issue_content(body, db)

    html_body = resolved.html_body or newsletter_publisher.text_to_html(resolved.text_body or "")
    scheduled_for = _parse_scheduled_for(body.scheduled_for)

    issue, created = await newsletter_issue_queue.enqueue_issue(
        db,
        title=resolved.title,
        html_body=html_body,
        slug=body.slug,
        newsletter_name=body.newsletter_name,
        seo_title=body.seo_title,
        seo_description=(resolved.seo_description or "")[:500] or None,
        post_description=resolved.post_description,
        issue_number=body.issue_number,
        category=resolved.category,
        scheduled_for=scheduled_for,
        dry_run=body.dry_run,
        cover_base64=body.cover_base64,
        render_cover=body.render_cover,
    )

    if body.media:
        issue.result = {**(issue.result or {}), newsletter_publisher.MEDIA_SPEC_KEY: body.media}
        await db.commit()

    due = body.dispatch_now or (scheduled_for is None) or (scheduled_for is not None and scheduled_for <= datetime.now(UTC))
    if created and issue.status.value == "queued" and due:
        try:
            from app.worker.tasks.newsletter_issues import dispatch_newsletter_issues

            dispatch_newsletter_issues.delay()
        except Exception:  # noqa: BLE001 — enqueue must not fail on broker hiccups
            pass

    return _issue_out(issue, created=created) | {
        "source": resolved.source,
        "word_count": resolved.words,
        "warnings": resolved.warnings,
    }


@router.get("/issues")
async def list_newsletter_issues(
    request: Request,
    status: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    _require_service_token(request)
    issues = await newsletter_issue_queue.list_issues(db, status=status, limit=limit)
    return {"issues": [_issue_out(i) for i in issues]}


@router.get("/issues/{issue_id}")
async def get_newsletter_issue(issue_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_service_token(request)
    iid = _parse_issue_uuid(issue_id)
    issue = await newsletter_issue_queue.get_issue(db, iid)
    if issue is None:
        raise HTTPException(status_code=404, detail="issue not found")
    return _issue_out(issue)


@router.post("/issues/{issue_id}/cancel")
async def cancel_newsletter_issue(issue_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_service_token(request)
    iid = _parse_issue_uuid(issue_id)
    issue = await newsletter_issue_queue.get_issue(db, iid)
    if issue is None:
        raise HTTPException(status_code=404, detail="issue not found")
    if not await newsletter_issue_queue.cancel_issue(db, issue):
        raise HTTPException(status_code=409, detail=f"cannot cancel issue in status {issue.status}")
    return _issue_out(issue)


@router.post("/issues/{issue_id}/retry")
async def retry_newsletter_issue(issue_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_service_token(request)
    iid = _parse_issue_uuid(issue_id)
    issue = await newsletter_issue_queue.get_issue(db, iid)
    if issue is None:
        raise HTTPException(status_code=404, detail="issue not found")
    if not await newsletter_issue_queue.retry_issue(db, issue):
        raise HTTPException(status_code=409, detail=f"cannot retry issue in status {issue.status}")
    return _issue_out(issue)


@router.post("/issues/{issue_id}/dispatch")
async def dispatch_newsletter_issue_now(issue_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Pull a queued issue to now and fire the dispatch task immediately."""
    _require_service_token(request)
    iid = _parse_issue_uuid(issue_id)
    issue = await newsletter_issue_queue.get_issue(db, iid)
    if issue is None:
        raise HTTPException(status_code=404, detail="issue not found")
    await newsletter_issue_queue.mark_due_now(db, issue)
    try:
        from app.worker.tasks.newsletter_issues import dispatch_newsletter_issues

        dispatch_newsletter_issues.delay()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"dispatch task unavailable: {exc}") from exc
    return _issue_out(issue) | {"dispatched": True}


@router.post("/issues/{issue_id}/verify")
async def verify_newsletter_issue_now(issue_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Verify the published article_url live through the sidecar browser."""
    _require_service_token(request)
    iid = _parse_issue_uuid(issue_id)
    issue = await newsletter_issue_queue.get_issue(db, iid)
    if issue is None:
        raise HTTPException(status_code=404, detail="issue not found")
    token = await newsletter_issue_queue.acquire_dispatch_lock()
    if token is None:
        raise HTTPException(status_code=409, detail="a newsletter publish/verify is already running")
    try:
        result = await newsletter_issue_queue.verify_issue_article(db, issue)
    finally:
        await newsletter_issue_queue.release_dispatch_lock(token)
    return _issue_out(issue) | {"verify": result}
