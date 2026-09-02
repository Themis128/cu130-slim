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
from app.models.analytics import AnalyticsEvent, FollowerSnapshot, PostAnalyticsSnapshot
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
    from urllib.parse import unquote

    pid = unquote(platform_post_id.strip())
    if pid.startswith("urn:li:"):
        return pid
    # Bare numeric ids are ambiguous (share vs ugcPost); discovery/alt tries both.
    if pid.isdigit():
        return None
    return pid


def _org_urn(account: SocialAccount) -> str:
    meta = account.meta_data or {}
    if meta.get("author_urn") and str(meta["author_urn"]).startswith("urn:li:organization:"):
        return str(meta["author_urn"])
    return f"urn:li:organization:{account.account_id}"


def _restli_list(urns: list[str]) -> str:
    """Rest.li List(...) with each URN percent-encoded; parentheses unencoded."""
    return "List(" + ",".join(quote(u, safe="") for u in urns) + ")"


def _urn_kind(urn: str) -> str | None:
    if "ugcPost" in urn:
        return "ugcPosts"
    if ":share:" in urn or urn.startswith("urn:li:share:"):
        return "shares"
    return None


def _alt_urn(urn: str) -> str | None:
    """Try the other share/ugcPost form with the same numeric id."""
    if "ugcPost" in urn:
        return "urn:li:share:" + urn.rsplit(":", 1)[-1]
    if ":share:" in urn:
        return "urn:li:ugcPost:" + urn.rsplit(":", 1)[-1]
    return None


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


def _is_hard_stats_failure(status: int, body: str) -> bool:
    """True when the failure should surface as a digest warning."""
    if status >= 500:
        return True
    low = (body or "").lower()
    # Missing activity / unknown post is common for stale local ids — soft skip.
    if "activityids" in low or "could not find entity" in low or "not_found" in low:
        return False
    if status in (401, 403):
        return True
    return status >= 400


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

    async def _request(param_name: str, urns: list[str]) -> httpx.Response | None:
        if not urns:
            return None
        url = (
            f"{base}?q=organizationalEntity&organizationalEntity={org_q}"
            f"&{param_name}={_restli_list(urns)}"
        )
        return await client.get(url, headers=headers)

    async def _stats_one(urn: str) -> MetricBundle:
        tried: set[str] = set()
        last_err: str | None = None
        attempts: list[tuple[str, str]] = []
        kind = _urn_kind(urn)
        if kind:
            attempts.append((kind, urn))
        else:
            attempts.append(("ugcPosts", urn))
            attempts.append(("shares", urn))
        alt = _alt_urn(urn)
        if alt:
            alt_kind = _urn_kind(alt)
            if alt_kind:
                attempts.append((alt_kind, alt))

        for param, candidate in attempts:
            key = f"{param}:{candidate}"
            if key in tried:
                continue
            tried.add(key)
            resp = await _request(param, [candidate])
            if resp is None:
                continue
            if resp.status_code < 400:
                data = resp.json() or {}
                for el in data.get("elements") or []:
                    parsed_urn, bundle = _parse_share_stats_element(el)
                    if parsed_urn:
                        return bundle
                return MetricBundle(raw={"note": "no_stats_element", "requested": candidate})
            last_err = f"linkedin stats HTTP {resp.status_code}: {resp.text[:220]}"
            if not _is_hard_stats_failure(resp.status_code, resp.text):
                # Soft failure — try alternate form before giving up.
                continue
        if last_err and _is_hard_stats_failure(400, last_err):
            return MetricBundle(notes=last_err)
        return MetricBundle(
            notes="stats_unavailable",
            raw={"requested": urn, "error": last_err},
        )

    # Partition known kinds for efficient batching; unknowns go one-by-one.
    ugc = [u for u in post_urns if _urn_kind(u) == "ugcPosts"]
    shares = [u for u in post_urns if _urn_kind(u) == "shares"]
    other = [u for u in post_urns if _urn_kind(u) is None]

    batch_size = 10
    for param_name, urns in (("ugcPosts", ugc), ("shares", shares)):
        for i in range(0, len(urns), batch_size):
            batch = urns[i : i + batch_size]
            resp = await _request(param_name, batch)
            if resp is None:
                continue
            if resp.status_code >= 400:
                # Batch failed (often one bad URN) — retry individually.
                for urn in batch:
                    out[urn] = await _stats_one(urn)
                continue
            data = resp.json() or {}
            found: set[str] = set()
            for el in data.get("elements") or []:
                parsed_urn, bundle = _parse_share_stats_element(el)
                if parsed_urn:
                    out[parsed_urn] = bundle
                    found.add(parsed_urn)
                    # Also map back if API returns share for a ugc request id space
                    for req in batch:
                        if req.rsplit(":", 1)[-1] == parsed_urn.rsplit(":", 1)[-1]:
                            out.setdefault(req, bundle)
            for urn in batch:
                out.setdefault(urn, MetricBundle(raw={"note": "no_stats_element"}))

    for urn in other:
        out[urn] = await _stats_one(urn)

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
    platform: str | None = None,
) -> None:
    plat = platform or account.platform
    if metrics.notes and metrics.notes.startswith("linkedin stats HTTP"):
        # Soft skips (stale/missing activity) should not pollute digest warnings.
        if _is_hard_stats_failure(400, metrics.notes):
            result.errors.append(f"{platform_post_id}: {metrics.notes}")
        else:
            result.skipped += 1
            return
    if metrics.notes == "stats_unavailable":
        result.skipped += 1
        return
    snap = PostAnalyticsSnapshot(
        team_id=account.team_id,
        post_id=post_id,
        social_account_id=account.id,
        platform=plat,
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
        platform=plat,
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


# ── Twitter / X ───────────────────────────────────────────────────────────────

async def _fetch_twitter_metrics(client: httpx.AsyncClient, token: str, tweet_id: str) -> MetricBundle:
    """Fetch public metrics for a single tweet via API v2."""
    url = f"https://api.x.com/2/tweets/{tweet_id}"
    params = {"tweet.fields": "public_metrics,non_public_metrics"}
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        return MetricBundle(notes=f"twitter stats HTTP {resp.status_code}")
    data = (resp.json() or {}).get("data", {})
    pm = data.get("public_metrics", {}) or {}
    npm = data.get("non_public_metrics", {}) or {}
    return MetricBundle(
        impressions=int(pm.get("impression_count", 0) or npm.get("impression_count", 0) or 0),
        clicks=int(npm.get("url_link_clicks", 0) or pm.get("url_link_clicks", 0) or 0),
        likes=int(pm.get("like_count", 0) or 0),
        comments=int(pm.get("reply_count", 0) or 0),
        shares=int(pm.get("retweet_count", 0) or 0),
        reach=int(pm.get("impression_count", 0) or 0),
        raw=data,
    )


async def sync_twitter_account(
    db: AsyncSession,
    account: SocialAccount,
    *,
    days: int = 365,
) -> SyncResult:
    result = SyncResult()
    token = decrypt_token(account.access_token_enc)
    since = datetime.now(UTC) - timedelta(days=days)
    captured_at = datetime.now(UTC)

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
    targets = [t for t in targets if (t.published_at or t.post.published_at or t.post.created_at) >= since]

    async with httpx.AsyncClient(timeout=30.0) as client:
        for t in targets:
            tweet_id = (t.platform_post_id or "").split("/")[-1]
            if not tweet_id:
                continue
            metrics = await _fetch_twitter_metrics(client, token, tweet_id)
            await _persist_snapshot(
                db, account=account, post_id=t.post_id, platform_post_id=tweet_id,
                metrics=metrics, captured_at=captured_at, source="twitter_api",
                result=result, platform="twitter",
            )

    if result.synced == 0:
        result.skipped = len(targets)
    await db.commit()
    return result


# ── Facebook ──────────────────────────────────────────────────────────────────

async def _fetch_facebook_post_metrics(
    client: httpx.AsyncClient, page_token: str, post_id: str,
) -> MetricBundle:
    """Fetch insights for a Facebook page post via Graph API."""
    url = f"https://graph.facebook.com/v20.0/{post_id}/insights"
    params = {
        "metric": "post_impressions,post_clicks,post_reactions_like_total,post_comments,post_shares",
        "access_token": page_token,
    }
    resp = await client.get(url, params=params)
    if resp.status_code != 200:
        return MetricBundle(notes=f"facebook stats HTTP {resp.status_code}")
    data = resp.json() or {}
    raw_metrics = {item["name"]: item for item in data.get("data", [])}

    def _val(name: str, idx: int = 0) -> int:
        item = raw_metrics.get(name)
        if not item:
            return 0
        values = item.get("values", [])
        if idx < len(values):
            return int(values[idx].get("value", 0) or 0)
        return 0

    return MetricBundle(
        impressions=_val("post_impressions"),
        clicks=_val("post_clicks"),
        likes=_val("post_reactions_like_total"),
        comments=_val("post_comments"),
        shares=_val("post_shares"),
        reach=_val("post_impressions"),
        raw=data,
    )


async def sync_facebook_account(
    db: AsyncSession,
    account: SocialAccount,
    *,
    days: int = 365,
) -> SyncResult:
    result = SyncResult()
    token = decrypt_token(account.access_token_enc)
    page_id = account.account_id
    since = datetime.now(UTC) - timedelta(days=days)
    captured_at = datetime.now(UTC)

    # Get page token
    page_token = token
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            "https://graph.facebook.com/v20.0/me/accounts",
            params={"access_token": token},
        )
        if resp.status_code == 200:
            for acct in (resp.json() or {}).get("data", []):
                if acct.get("id") == page_id:
                    page_token = acct.get("access_token", token)
                    break

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
        targets = [t for t in targets if (t.published_at or t.post.published_at or t.post.created_at) >= since]

        for t in targets:
            fb_post_id = t.platform_post_id or ""
            if not fb_post_id:
                continue
            metrics = await _fetch_facebook_post_metrics(client, page_token, fb_post_id)
            await _persist_snapshot(
                db, account=account, post_id=t.post_id, platform_post_id=fb_post_id,
                metrics=metrics, captured_at=captured_at, source="facebook_api",
                result=result, platform="facebook",
            )

    if result.synced == 0:
        result.skipped = len(targets)
    await db.commit()
    return result


# ── Instagram ─────────────────────────────────────────────────────────────────

async def _fetch_instagram_media_metrics(
    client: httpx.AsyncClient, token: str, ig_user_id: str, media_id: str,
) -> MetricBundle:
    """Fetch insights for an Instagram media post via Graph API."""
    url = f"https://graph.facebook.com/v20.0/{media_id}/insights"
    params = {
        "metric": "impressions,reach,likes,comments,saves",
        "access_token": token,
    }
    resp = await client.get(url, params=params)
    if resp.status_code != 200:
        return MetricBundle(notes=f"instagram stats HTTP {resp.status_code}")
    data = resp.json() or {}
    raw_metrics = {item["name"]: item for item in data.get("data", [])}

    def _val(name: str) -> int:
        item = raw_metrics.get(name)
        if not item:
            return 0
        values = item.get("values", [])
        return int(values[0].get("value", 0) or 0) if values else 0

    return MetricBundle(
        impressions=_val("impressions"),
        likes=_val("likes"),
        comments=_val("comments"),
        reach=_val("reach"),
        raw=data,
    )


async def sync_instagram_account(
    db: AsyncSession,
    account: SocialAccount,
    *,
    days: int = 365,
) -> SyncResult:
    result = SyncResult()
    token = decrypt_token(account.access_token_enc)
    ig_user_id = account.account_id
    since = datetime.now(UTC) - timedelta(days=days)
    captured_at = datetime.now(UTC)

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
    targets = [t for t in targets if (t.published_at or t.post.published_at or t.post.created_at) >= since]

    async with httpx.AsyncClient(timeout=30.0) as client:
        for t in targets:
            media_id = t.platform_post_id or ""
            if not media_id:
                continue
            metrics = await _fetch_instagram_media_metrics(client, token, ig_user_id, media_id)
            await _persist_snapshot(
                db, account=account, post_id=t.post_id, platform_post_id=media_id,
                metrics=metrics, captured_at=captured_at, source="instagram_api",
                result=result, platform="instagram",
            )

    if result.synced == 0:
        result.skipped = len(targets)
    await db.commit()
    return result


# ── Threads ───────────────────────────────────────────────────────────────────

async def _fetch_threads_media_metrics(
    client: httpx.AsyncClient, token: str, media_id: str,
) -> MetricBundle:
    """Fetch insights for a Threads media post via Threads API."""
    url = f"https://graph.threads.net/v1.0/{media_id}/insights"
    params = {"metric": "views,likes,replies,reposts,quotes"}
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        return MetricBundle(notes=f"threads stats HTTP {resp.status_code}")
    data = resp.json() or {}
    raw_metrics = {item["name"]: item for item in data.get("data", [])}

    def _val(name: str) -> int:
        item = raw_metrics.get(name)
        if not item:
            return 0
        values = item.get("values", [])
        return int(values[0].get("value", 0) or 0) if values else 0

    return MetricBundle(
        impressions=_val("views"),
        likes=_val("likes"),
        comments=_val("replies"),
        shares=_val("reposts") + _val("quotes"),
        reach=_val("views"),
        raw=data,
    )


async def sync_threads_account(
    db: AsyncSession,
    account: SocialAccount,
    *,
    days: int = 365,
) -> SyncResult:
    result = SyncResult()
    token = decrypt_token(account.access_token_enc)
    since = datetime.now(UTC) - timedelta(days=days)
    captured_at = datetime.now(UTC)

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
    targets = [t for t in targets if (t.published_at or t.post.published_at or t.post.created_at) >= since]

    async with httpx.AsyncClient(timeout=30.0) as client:
        for t in targets:
            media_id = t.platform_post_id or ""
            if not media_id:
                continue
            metrics = await _fetch_threads_media_metrics(client, token, media_id)
            await _persist_snapshot(
                db, account=account, post_id=t.post_id, platform_post_id=media_id,
                metrics=metrics, captured_at=captured_at, source="threads_api",
                result=result, platform="threads",
            )

    if result.synced == 0:
        result.skipped = len(targets)
    await db.commit()
    return result


# ── TikTok ────────────────────────────────────────────────────────────────────

async def _fetch_tiktok_video_stats(
    client: httpx.AsyncClient, token: str, video_id: str,
) -> MetricBundle:
    """Fetch stats for a TikTok video via TikTok Display API.

    Uses the /v2/video/list/ endpoint which returns video stats for the
    authenticated user's own videos. Requires video.list scope.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    # The Display API video/list endpoint fetches the user's own videos
    # with stats. We filter by the specific video_id we're interested in.
    url = "https://open.tiktokapis.com/v2/video/list/"
    params = {"fields": "id,view_count,like_count,comment_count,share_count,reach_count"}
    resp = await client.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        return MetricBundle(notes=f"tiktok stats HTTP {resp.status_code}")
    data = resp.json() or {}
    videos = (data.get("data") or {}).get("videos", [])
    for v in videos:
        if v.get("id") == video_id:
            return MetricBundle(
                impressions=int(v.get("view_count", 0) or 0),
                likes=int(v.get("like_count", 0) or 0),
                comments=int(v.get("comment_count", 0) or 0),
                shares=int(v.get("share_count", 0) or 0),
                reach=int(v.get("reach_count", 0) or v.get("view_count", 0) or 0),
                raw=v,
            )
    return MetricBundle(notes="tiktok_video_not_found_in_list")


async def sync_tiktok_account(
    db: AsyncSession,
    account: SocialAccount,
    *,
    days: int = 365,
) -> SyncResult:
    result = SyncResult()
    token = decrypt_token(account.access_token_enc)
    since = datetime.now(UTC) - timedelta(days=days)
    captured_at = datetime.now(UTC)

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
    targets = [t for t in targets if (t.published_at or t.post.published_at or t.post.created_at) >= since]

    async with httpx.AsyncClient(timeout=30.0) as client:
        for t in targets:
            video_id = t.platform_post_id or ""
            if not video_id:
                continue
            metrics = await _fetch_tiktok_video_stats(client, token, video_id)
            await _persist_snapshot(
                db, account=account, post_id=t.post_id, platform_post_id=video_id,
                metrics=metrics, captured_at=captured_at, source="tiktok_api",
                result=result, platform="tiktok",
            )

    if result.synced == 0:
        result.skipped = len(targets)
    await db.commit()
    return result


# ── Dispatch ──────────────────────────────────────────────────────────────────

async def sync_team_analytics(
    db: AsyncSession,
    team_id: uuid.UUID,
    *,
    days: int = 365,
    platforms: list[str] | None = None,
) -> SyncResult:
    """Sync all active accounts for a team across all six platforms."""
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
            elif platform == "twitter":
                r = await sync_twitter_account(db, account, days=days)
            elif platform == "facebook":
                r = await sync_facebook_account(db, account, days=days)
            elif platform == "instagram":
                r = await sync_instagram_account(db, account, days=days)
            elif platform == "threads":
                r = await sync_threads_account(db, account, days=days)
            elif platform == "tiktok":
                r = await sync_tiktok_account(db, account, days=days)
            else:
                combined.skipped += 1
                combined.errors.append(f"{platform}:{account.username}: unsupported platform")
                continue
            combined.synced += r.synced
            combined.skipped += r.skipped
            combined.errors.extend(r.errors)
            combined.snapshots.extend(r.snapshots)
            # Persist a follower snapshot for this account so we can chart
            # follower growth over time without re-fetching from each API.
            await _persist_follower_snapshot(db, account)
        except Exception as exc:  # noqa: BLE001
            combined.errors.append(f"{platform}:{account.username}: {exc}")
    await db.commit()
    return combined


async def _persist_follower_snapshot(db: AsyncSession, account: SocialAccount) -> None:
    """Fetch the live follower count for *account* and append a FollowerSnapshot row.

    Failures are logged but never raised — follower tracking is a nice-to-have
    and must not break the analytics sync.
    """
    try:
        from app.api.analytics import _follower_count

        followers = await _follower_count(account)
        if followers < 0:
            return
        snap = FollowerSnapshot(
            team_id=account.team_id,
            social_account_id=account.id,
            platform=account.platform,
            followers=followers,
        )
        db.add(snap)
    except Exception:
        pass  # follower tracking is best-effort
