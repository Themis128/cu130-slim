"""Export SocialAuto data to the cloudless.gr R2 datalake.

Writes JSON tables to ``lake/socialauto-*/`` in the datalake bucket so the
site's ``materialize-datalake-snapshots`` ETL can build gold sections:

    lake/socialauto-accounts/accounts.json
    lake/socialauto-posts/posts.json
    lake/socialauto-post-metrics/metrics.json
    lake/socialauto-followers/followers.json
    lake/socialauto-account-events/events.json
    lake/socialauto-insights/<team>.json
    lake/socialauto-leads/leads.json
    lake/socialauto-web-events/events.json

Each run overwrites the whole table (snapshot-style export) — idempotent
by construction, no dedup needed downstream. PII is minimized: leads keep
only a sha256 email hash + domain; web events drop client_ip/user_agent.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.analytics import AnalyticsEvent, FollowerSnapshot, PostAnalyticsSnapshot
from app.models.content import Post, PostTarget
from app.models.lead import Lead
from app.models.social_account import SocialAccount
from app.models.user import Team
from app.models.web_analytics import WebAnalyticsEvent
from app.services import r2_storage
from app.services.insights_engine import build_team_insights
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

settings = get_settings()

_WEB_EVENTS_DAYS = 90


@asynccontextmanager
async def _worker_db():
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def _email_hash(email: str | None) -> str | None:
    e = (email or "").strip().lower()
    return hashlib.sha256(e.encode()).hexdigest()[:16] if e else None


def _email_domain(email: str | None) -> str | None:
    e = (email or "").strip().lower()
    return e.rsplit("@", 1)[-1] if "@" in e else None


async def _put_json(key: str, rows: Any, bucket: str) -> dict:
    data = json.dumps(rows, ensure_ascii=False, default=str).encode()
    return await r2_storage.upload_object(
        key, data, content_type="application/json", bucket=bucket
    )


async def _export_accounts(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(select(SocialAccount))).scalars().all()
    return [
        {
            "account_id": str(a.id),
            "team_id": str(a.team_id),
            "platform": a.platform,
            "username": a.username,
            "display_name": a.display_name,
            "account_type": a.account_type,
            "is_business": a.is_business,
            "status": a.status,
            "scopes": sorted(a.scopes or []),
            "token_expires_at": _iso(a.token_expires_at),
            "connected_at": _iso(a.created_at),
        }
        for a in rows
    ]


async def _export_posts(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(Post, PostTarget, SocialAccount.platform)
        .join(PostTarget, PostTarget.post_id == Post.id)
        .join(SocialAccount, SocialAccount.id == PostTarget.social_account_id)
        .order_by(Post.created_at.desc())
    )
    by_post: dict[str, dict] = {}
    for post, target, platform in result.all():
        key = str(post.id)
        entry = by_post.setdefault(key, {
            "post_id": key,
            "team_id": str(post.team_id),
            "status": str(post.status),
            "content_snippet": (post.content_text or "")[:200],
            "link_url": post.link_url,
            "hashtags": list(post.hashtags or []),
            "media_count": len(post.media_ids or []),
            "scheduled_at": _iso(post.scheduled_at),
            "published_at": _iso(post.published_at),
            "created_at": _iso(post.created_at),
            "targets": [],
        })
        entry["targets"].append({
            "account_id": str(target.social_account_id),
            "platform": platform,
            "platform_post_id": target.platform_post_id,
            "platform_url": target.platform_url,
            "status": target.status,
        })
    return list(by_post.values())


async def _export_metrics(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(PostAnalyticsSnapshot).order_by(
                PostAnalyticsSnapshot.captured_at.desc()
            )
        )
    ).scalars().all()
    return [
        {
            "snapshot_id": str(s.id),
            "team_id": str(s.team_id),
            "post_id": str(s.post_id) if s.post_id else None,
            "account_id": str(s.social_account_id),
            "platform": s.platform,
            "platform_post_id": s.platform_post_id,
            "impressions": s.impressions,
            "clicks": s.clicks,
            "likes": s.likes,
            "comments": s.comments,
            "shares": s.shares,
            "reach": s.reach,
            "engagement": s.engagement,
            "engagement_rate": s.engagement_rate,
            "source": s.source,
            "notes": s.notes,
            "captured_at": _iso(s.captured_at),
        }
        for s in rows
    ]


async def _export_followers(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(FollowerSnapshot).order_by(FollowerSnapshot.captured_at)
        )
    ).scalars().all()
    return [
        {
            "account_id": str(f.social_account_id),
            "team_id": str(f.team_id),
            "platform": f.platform,
            "followers": f.followers,
            "captured_at": _iso(f.captured_at),
        }
        for f in rows
    ]


async def _export_account_events(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(AnalyticsEvent)
            .where(AnalyticsEvent.post_id.is_(None))
            .order_by(AnalyticsEvent.occurred_at.desc())
        )
    ).scalars().all()
    return [
        {
            "team_id": str(e.team_id),
            "account_id": str(e.social_account_id) if e.social_account_id else None,
            "platform": e.platform,
            "event_type": e.event_type,
            "meta_data": e.meta_data or {},
            "occurred_at": _iso(e.occurred_at),
        }
        for e in rows
    ]


async def _export_leads(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(Lead, SocialAccount.platform)
            .outerjoin(SocialAccount, SocialAccount.id == Lead.social_account_id)
            .order_by(Lead.created_at.desc())
        )
    ).all()
    return [
        {
            "lead_id": str(lead.id),
            "team_id": str(lead.team_id),
            "source": str(lead.source),
            "platform": platform,
            "interest": str(lead.interest) if lead.interest else None,
            "company_size": str(lead.company_size) if lead.company_size else None,
            # PII-minimized: hash supports dedup/joins, domain supports
            # segmentation — no raw email/name in the lake.
            "email_hash": _email_hash(lead.email),
            "email_domain": _email_domain(lead.email),
            "espocrm_synced": bool((lead.meta_data or {}).get("espocrm_lead_id")),
            "created_at": _iso(lead.created_at),
        }
        for lead, platform in rows
    ]


async def _export_web_events(db: AsyncSession) -> list[dict]:
    since = datetime.now(UTC) - timedelta(days=_WEB_EVENTS_DAYS)
    rows = (
        await db.execute(
            select(WebAnalyticsEvent)
            .where(WebAnalyticsEvent.occurred_at >= since)
            .order_by(WebAnalyticsEvent.occurred_at.desc())
        )
    ).scalars().all()
    out = []
    for e in rows:
        payload = e.payload or {}
        out.append({
            "team_id": str(e.team_id),
            "domain": e.domain,
            "event_name": e.event_name,
            "session_id": e.session_id,
            "path": e.path,
            "referrer": e.referrer,
            "locale": e.locale,
            "utm_source": payload.get("utm_source"),
            "utm_medium": payload.get("utm_medium"),
            "utm_campaign": payload.get("utm_campaign"),
            # client_ip / user_agent intentionally dropped (PII minimization)
            "occurred_at": _iso(e.occurred_at),
        })
    return out


@shared_task
def export_datalake() -> dict:
    """Snapshot-export all SocialAuto lake tables to the datalake bucket."""
    return asyncio.run(_export_async())


async def _export_async() -> dict:
    bucket = (settings.DATALAKE_R2_BUCKET or "").strip()
    if not bucket:
        return {"ok": False, "error": "DATALAKE_R2_BUCKET not configured"}
    if not (settings.CLOUDFLARE_ACCOUNT_ID and settings.CLOUDFLARE_API_TOKEN):
        return {"ok": False, "error": "Cloudflare credentials not configured"}

    async with _worker_db() as db:
        tables: dict[str, Any] = {
            "lake/socialauto-accounts/accounts.json": await _export_accounts(db),
            "lake/socialauto-posts/posts.json": await _export_posts(db),
            "lake/socialauto-post-metrics/metrics.json": await _export_metrics(db),
            "lake/socialauto-followers/followers.json": await _export_followers(db),
            "lake/socialauto-account-events/events.json": await _export_account_events(db),
            "lake/socialauto-leads/leads.json": await _export_leads(db),
            "lake/socialauto-web-events/events.json": await _export_web_events(db),
        }

        # Insights engine output — one gold-ready file per team.
        teams = (await db.execute(select(Team))).scalars().all()
        for team in teams:
            try:
                insights = await build_team_insights(db, team.id)
                tables[f"lake/socialauto-insights/{team.id}.json"] = insights
            except Exception as exc:  # noqa: BLE001 — export others anyway
                tables[f"lake/socialauto-insights/{team.id}.json"] = {
                    "error": str(exc)[:300]
                }

    written = {}
    for key, rows in tables.items():
        try:
            res = await _put_json(key, rows, bucket)
            written[key] = res.get("size", 0)
        except Exception as exc:  # noqa: BLE001 — report, don't abort others
            written[key] = f"ERROR: {exc}"

    errors = {k: v for k, v in written.items() if isinstance(v, str)}
    return {
        "ok": not errors,
        "bucket": bucket,
        "files": len(written),
        "bytes": sum(v for v in written.values() if isinstance(v, int)),
        "errors": errors,
    }
