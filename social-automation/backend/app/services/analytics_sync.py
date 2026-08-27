"""Pull post analytics from connected platforms and store in Postgres.

Primary: LinkedIn organization share statistics (Company Page posts).
All metrics are persisted as PostAnalyticsSnapshot rows (+ upserted
AnalyticsEvent counters with meta_data.count for dashboard aggregates).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import decrypt_token
from app.models.analytics import AnalyticsEvent, PostAnalyticsSnapshot
from app.models.content import Post, PostStatus, PostTarget
from app.models.social_account import SocialAccount

LINKEDIN_VERSION = "202608"
ENGAGEMENT_TYPES = ("impression", "click", "like", "comment", "share")


@dataclass
class MetricBundle:
    impressions: int = 0
    clicks: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    reach: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None

    @property
    def engagement(self) -> int:
        return self.likes + self.comments + self.shares + self.clicks

    @property
    def engagement_rate(self) -> float:
        if self.impressions <= 0:
            return 0.0
        return self.engagement / self.impressions


@dataclass
class SyncResult:
    synced: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    snapshots: list[str] = field(default_factory=list)


def _linkedin_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": LINKEDIN_VERSION,
    }


def _normalize_post_urn(platform_post_id: str | None) -> str | None:
    if not platform_post_id:
        return None
    pid = platform_post_id.strip()
    if pid.startswith("urn:li:"):
        return pid
    if pid.isdigit():
        return f"urn:li:ugcPost:{pid}"
    return pid


def _org_urn(account: SocialAccount) -> str:
    meta = account.meta_data or {}
    if meta.get("author_urn") and str(meta["author_urn"]).startswith("urn:li:organization:"):
        return str(meta["author_urn"])
    return f"urn:li:organization:{account.account_id}"


def _restli_list(urns: list[str]) -> str:
    """Rest.li List(...) with each URN percent-encoded; parentheses unencoded."""
    return "List(" + ",".join(quote(u, safe="") for u in urns) + ")"


def _parse_share_stats_element(el: dict[str, Any]) -> tuple[str | None, MetricBundle]:
    stats = el.get("totalShareStatistics") or {}
    urn = el.get("ugcPost") or el.get("share")
    bundle = MetricBundle(
        impressions=int(stats.get("impressionCount") or 0),
        clicks=int(stats.get("clickCount") or 0),
        likes=int(stats.get("likeCount") or 0),
        comments=int(stats.get("commentCount") or 0),
        shares=int(stats.get("shareCount") or 0),
        reach=int(stats.get("uniqueImpressionsCount") or stats.get("uniqueImpressions") or 0),
        raw={"organizationalEntityShareStatistics": el},
    )
    return urn, bundle


async def _fetch_linkedin_org_stats(
    client: httpx.AsyncClient,
    token: str,
    org_urn: str,
    post_urns: list[str],
) -> dict[str, MetricBundle]:
    """Lifetime stats for org posts. Uses Rest.li List() encoding (indexed [] is rejected)."""
    if not post_urns:
        return {}

    headers = _linkedin_headers(token)
    out: dict[str, MetricBundle] = {}
    base = "https://api.linkedin.com/rest/organizationalEntityShareStatistics"
    org_q = quote(org_urn, safe="")

    batch_size = 10
    for i in range(0, len(post_urns), batch_size):
        batch = post_urns[i : i + batch_size]
        ugc = [u for u in batch if "ugcPost" in u]
        shares = [u for u in batch if "ugcPost" not in u]

        async def _request(param_name: str, urns: list[str]) -> httpx.Response | None:
            if not urns:
                return None
            url = (
                f"{base}?q=organizationalEntity&organizationalEntity={org_q}"
                f"&{param_name}={_restli_list(urns)}"
            )
            return await client.get(url, headers=headers)

        for param_name, urns in (("ugcPosts", ugc), ("shares", shares)):
            resp = await _request(param_name, urns)
            if resp is None:
                continue
            if resp.status_code >= 400:
                for urn in urns:
                    out.setdefault(
                        urn,
                        MetricBundle(notes=f"linkedin stats HTTP {resp.status_code}: {resp.text[:220]}"),
                    )
                continue
            data = resp.json()
            found: set[str] = set()
            for el in data.get("elements") or []:
                urn, bundle = _parse_share_stats_element(el)
                if urn:
                    out[urn] = bundle
                    found.add(urn)
            for urn in urns:
                out.setdefault(urn, MetricBundle(raw={"note": "no_stats_element"}))

    return out


async def _fetch_org_lifetime_stats(
    client: httpx.AsyncClient,
    token: str,
    org_urn: str,
) -> MetricBundle:
    headers = _linkedin_headers(token)
    url = (
        "https://api.linkedin.com/rest/organizationalEntityShareStatistics"
        f"?q=organizationalEntity&organizationalEntity={quote(org_urn, safe='')}"
    )
    resp = await client.get(url, headers=headers)
    if resp.status_code >= 400:
        return MetricBundle(notes=f"org lifetime HTTP {resp.status_code}: {resp.text[:200]}")
    elements = (resp.json() or {}).get("elements") or []
    if not elements:
        return MetricBundle(raw={"note": "empty_org_lifetime"})
    _, bundle = _parse_share_stats_element(elements[0])
    bundle.notes = "organization_lifetime"
    return bundle


async def _list_org_post_urns(
    client: httpx.AsyncClient,
    token: str,
    org_urn: str,
    *,
    since: datetime,
    max_pages: int = 10,
) -> list[dict[str, Any]]:
    """Discover Company Page posts via Posts API (author finder)."""
    headers = _linkedin_headers(token)
    out: list[dict[str, Any]] = []
    start = 0
    page_size = 20
    for _ in range(max_pages):
        url = (
            "https://api.linkedin.com/rest/posts?q=author"
            f"&author={quote(org_urn, safe='')}"
            f"&count={page_size}&start={start}&sortBy=LAST_MODIFIED"
        )
        resp = await client.get(url, headers=headers)
        if resp.status_code >= 400:
            break
        data = resp.json() or {}
        elements = data.get("elements") or []
        if not elements:
            break
        for el in elements:
            pid = el.get("id")
            if not pid:
                continue
            pub_ms = el.get("publishedAt") or 0
            pub_at = datetime.fromtimestamp(pub_ms / 1000, tz=UTC) if pub_ms else None
            if pub_at and pub_at < since:
                continue
            out.append(
                {
                    "urn": pid,
                    "published_at": pub_at,
                    "commentary": el.get("commentary") or "",
                    "raw": el,
                }
            )
        total = (data.get("paging") or {}).get("total") or 0
        start += page_size
        if start >= total:
            break
    return out


async def _upsert_counter_events(
    db: AsyncSession,
    *,
    team_id: uuid.UUID,
    post_id: uuid.UUID | None,
    social_account_id: uuid.UUID,
    platform: str,
    platform_post_id: str | None,
    metrics: MetricBundle,
    captured_at: datetime,
) -> None:
    mapping = {
        "impression": metrics.impressions,
        "click": metrics.clicks,
        "like": metrics.likes,
        "comment": metrics.comments,
        "share": metrics.shares,
    }
    for event_type, count in mapping.items():
        if count <= 0:
            continue
        key = f"sync:{platform}:{platform_post_id}:{event_type}"[:200]
        existing = (
            await db.execute(
                select(AnalyticsEvent).where(AnalyticsEvent.platform_event_id == key)
            )
        ).scalar_one_or_none()
        meta = {
            "count": int(count),
            "source": "platform_sync",
            "platform_post_id": platform_post_id,
            "captured_at": captured_at.isoformat(),
        }
        if existing:
            existing.meta_data = meta
            existing.occurred_at = captured_at
            existing.post_id = post_id
            existing.social_account_id = social_account_id
        else:
            db.add(
                AnalyticsEvent(
                    team_id=team_id,
                    post_id=post_id,
                    social_account_id=social_account_id,
                    event_type=event_type,
                    platform=platform,
                    platform_event_id=key,
                    occurred_at=captured_at,
                    meta_data=meta,
                )
            )


async def _persist_snapshot(
    db: AsyncSession,
    *,
    account: SocialAccount,
    post_id: uuid.UUID | None,
    platform_post_id: str,
    metrics: MetricBundle,
    captured_at: datetime,
    source: str,
    result: SyncResult,
) -> None:
    if metrics.notes and metrics.notes.startswith("linkedin stats HTTP"):
        result.errors.append(f"{platform_post_id}: {metrics.notes}")
    snap = PostAnalyticsSnapshot(
        team_id=account.team_id,
        post_id=post_id,
        social_account_id=account.id,
        platform="linkedin",
        platform_post_id=platform_post_id,
        impressions=metrics.impressions,
        clicks=metrics.clicks,
        likes=metrics.likes,
        comments=metrics.comments,
        shares=metrics.shares,
        reach=metrics.reach,
        engagement=metrics.engagement,
        engagement_rate=metrics.engagement_rate,
        raw=metrics.raw,
        source=source,
        notes=metrics.notes,
        captured_at=captured_at,
    )
    db.add(snap)
    await _upsert_counter_events(
        db,
        team_id=account.team_id,
        post_id=post_id,
        social_account_id=account.id,
        platform="linkedin",
        platform_post_id=platform_post_id,
        metrics=metrics,
        captured_at=captured_at,
    )
    result.synced += 1
    result.snapshots.append(platform_post_id)


async def sync_linkedin_account(
    db: AsyncSession,
    account: SocialAccount,
    *,
    days: int = 365,
) -> SyncResult:
    result = SyncResult()
    meta = account.meta_data or {}
    account_type = str(meta.get("account_type") or "").lower()
    is_org = account_type in ("organization", "company", "page") or bool(meta.get("organization_id"))

    token = decrypt_token(account.access_token_enc)
    since = datetime.now(UTC) - timedelta(days=days)
    captured_at = datetime.now(UTC)

    # Local published targets with known platform IDs
    targets_q = (
        select(PostTarget)
        .join(Post, Post.id == PostTarget.post_id)
        .where(
            PostTarget.social_account_id == account.id,
            PostTarget.status == "published",
            PostTarget.platform_post_id.isnot(None),
            Post.status == PostStatus.PUBLISHED,
            Post.team_id == account.team_id,
        )
        .options(selectinload(PostTarget.post))
    )
    targets = (await db.execute(targets_q)).scalars().all()

    def _when(t: PostTarget) -> datetime:
        return t.published_at or t.post.published_at or t.post.created_at

    targets = [t for t in targets if _when(t) >= since]
    urn_to_post_id: dict[str, uuid.UUID | None] = {}
    for t in targets:
        urn = _normalize_post_urn(t.platform_post_id)
        if urn:
            urn_to_post_id[urn] = t.post_id

    async with httpx.AsyncClient(timeout=60.0) as client:
        discovery_meta: dict[str, dict] = {}
        if is_org:
            org = _org_urn(account)
            discovered = await _list_org_post_urns(client, token, org, since=since)
            for item in discovered:
                urn_to_post_id.setdefault(item["urn"], None)
                discovery_meta[item["urn"]] = {
                    "commentary": item.get("commentary") or "",
                    "published_at": item["published_at"].isoformat() if item.get("published_at") else None,
                    "post": item.get("raw"),
                }

            all_urns = list(urn_to_post_id.keys())
            stats_map = await _fetch_linkedin_org_stats(client, token, org, all_urns)
            org_lifetime = await _fetch_org_lifetime_stats(client, token, org)
        else:
            stats_map = {}
            org_lifetime = MetricBundle(notes="member_account_no_org_stats")
            all_urns = list(urn_to_post_id.keys())
            for urn in all_urns:
                stats_map[urn] = MetricBundle(notes="member_stats_not_implemented")

        for urn in all_urns:
            metrics = stats_map.get(urn) or MetricBundle(notes="missing_stats")
            if urn in discovery_meta:
                metrics.raw = {**(metrics.raw or {}), "discovery": discovery_meta[urn]}
            await _persist_snapshot(
                db,
                account=account,
                post_id=urn_to_post_id.get(urn),
                platform_post_id=urn,
                metrics=metrics,
                captured_at=captured_at,
                source="linkedin_org" if is_org else "linkedin_member",
                result=result,
            )

        if is_org and not (org_lifetime.notes or "").startswith("org lifetime HTTP"):
            await _persist_snapshot(
                db,
                account=account,
                post_id=None,
                platform_post_id=_org_urn(account),
                metrics=org_lifetime,
                captured_at=captured_at,
                source="linkedin_org_lifetime",
                result=result,
            )

    if result.synced == 0:
        result.skipped = len(targets)

    await db.commit()
    return result


async def sync_team_analytics(
    db: AsyncSession,
    team_id: uuid.UUID,
    *,
    days: int = 365,
    platforms: list[str] | None = None,
) -> SyncResult:
    """Sync all active accounts for a team. LinkedIn implemented; others skipped."""
    q = select(SocialAccount).where(
        SocialAccount.team_id == team_id,
        SocialAccount.status == "active",
    )
    if platforms:
        q = q.where(SocialAccount.platform.in_(platforms))
    accounts = (await db.execute(q)).scalars().all()

    combined = SyncResult()
    for account in accounts:
        platform = (account.platform or "").lower()
        try:
            if platform == "linkedin":
                r = await sync_linkedin_account(db, account, days=days)
                combined.synced += r.synced
                combined.skipped += r.skipped
                combined.errors.extend(r.errors)
                combined.snapshots.extend(r.snapshots)
            else:
                combined.skipped += 1
                combined.errors.append(f"{platform}:{account.username}: sync not implemented yet")
        except Exception as exc:  # noqa: BLE001
            combined.errors.append(f"{platform}:{account.username}: {exc}")
    return combined
