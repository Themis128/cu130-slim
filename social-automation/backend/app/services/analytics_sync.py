"""Pull post analytics from connected platforms and store in Postgres.

Primary: LinkedIn organization share statistics (Company Page posts).
All metrics are persisted as PostAnalyticsSnapshot rows (+ upserted
AnalyticsEvent counters with meta_data.count for dashboard aggregates).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.security import decrypt_token
from app.models.analytics import AnalyticsEvent, FollowerSnapshot, PostAnalyticsSnapshot
from app.models.content import Post, PostStatus, PostTarget
from app.models.social_account import SocialAccount
from app.services.meta_graph import facebook_graph_url

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
    notes: str = ""


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


def _meta_unsupported_metric_names(msg: str) -> set[str]:
    """Parse metric names Meta rejected for this media/product/API version.

    Handles several Graph error phrasings seen in the wild, e.g.:
    - "does not support the impressions, replies metric for this media product type"
    - "The impressions, replies metrics are not available for this media product type"
    - "(#100) metric impressions is not available for this media product type"
    """
    import re

    names: set[str] = set()
    patterns = (
        r"does not support the ([a-zA-Z0-9_ ,]+) metrics?",
        r"(?:The )?([a-zA-Z0-9_ ,]+) metrics? (?:is|are) not available",
        r"metric[s]?\s*\(?([a-zA-Z0-9_ ,]+)\)?\s+is not available",
    )
    for pat in patterns:
        m = re.search(pat, msg, flags=re.IGNORECASE)
        if not m:
            continue
        for raw in m.group(1).split(","):
            n = raw.strip().lower()
            # Bail out of garbage captures that aren't metric identifiers.
            if n and re.fullmatch(r"[a-z0-9_]+", n):
                names.add(n)
    return names


async def _meta_insights_get(
    client: httpx.AsyncClient,
    url: str,
    metrics: list[str],
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """GET a Meta insights endpoint, adaptively dropping rejected metrics.

    Meta deprecates/renames insight metrics per API version and token type
    (e.g. `post_impressions` deprecated June 2026, `saves` vs `saved`,
    `views` only on newer versions). On a 400 whose body says
    "metric[N] must be one of the following values", drop metric[N] and
    retry. Also drops metrics named in media-product-type rejection messages
    (impressions/replies on FEED, etc.). Returns the final response (200 or
    last error).
    """
    import re

    remaining = list(metrics)
    base = dict(params or {})
    resp: httpx.Response | None = None
    for _ in range(max(len(remaining) * 3, 1)):
        if not remaining:
            break
        req_params = {**base, "metric": ",".join(remaining)}
        resp = await client.get(url, headers=headers, params=req_params)
        if resp.status_code == 200:
            return resp
        if resp.status_code != 400:
            return resp
        try:
            msg = (resp.json() or {}).get("error", {}).get("message", "")
        except Exception:
            msg = ""

        # Pattern 1: metric[N] must be one of...
        m = re.search(r"metric\[(\d+)\]", msg)
        if m and "must be one of" in msg:
            idx = int(m.group(1))
            if idx < len(remaining):
                remaining.pop(idx)
            continue

        # Pattern 2/3: media product type / "not available" lists names.
        unsupported = _meta_unsupported_metric_names(msg)
        if unsupported:
            before = len(remaining)
            remaining = [m for m in remaining if m.lower() not in unsupported]
            if len(remaining) < before:
                continue

        return resp
    return resp if resp is not None else await client.get(
        url, headers=headers, params=base
    )


def _meta_error_message(resp: httpx.Response) -> str:
    """Extract Meta's error.message for warning notes."""
    try:
        return (resp.json() or {}).get("error", {}).get("message", "") or resp.text[:200]
    except Exception:
        return resp.text[:200]


def _persist_account_event(
    db: AsyncSession,
    account: SocialAccount,
    captured_at: datetime,
    event_type: str,
    metrics: dict[str, Any],
) -> None:
    """Write an account-level analytics event (insights/profile/audience).

    Post-level metrics live in PostAnalyticsSnapshot; account-level data
    (page views, reach, follower demographics, org lifetime totals) lives
    here so dashboards can chart growth/audience independent of posts.
    """
    db.add(AnalyticsEvent(
        team_id=account.team_id,
        social_account_id=account.id,
        platform=account.platform,
        event_type=event_type,
        occurred_at=captured_at,
        meta_data={"captured_at": captured_at.isoformat(), **metrics},
    ))


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

async def _fetch_linkedin_ad_stats(
    client: httpx.AsyncClient,
    token: str,
    ad_account_id: str,
    *,
    since: datetime,
) -> dict[str, MetricBundle]:
    """Campaign-level paid metrics via the Advertising Reporting API.

    Requires the ``r_ads_reporting`` scope — granted by the "Advertising
    Reporting API" product on the developer app (self-serve in development
    tier, ≤5 ad accounts). Returns one bundle per sponsoredCampaign URN with
    daily rows and spend in ``raw`` (MetricBundle has no spend field).
    """
    if not ad_account_id:
        return {}
    headers = _linkedin_headers(token)
    end = datetime.now(UTC)
    date_range = (
        f"(start:(year:{since.year},month:{since.month},day:{since.day}),"
        f"end:(year:{end.year},month:{end.month},day:{end.day}))"
    )
    fields = (
        "impressions,clicks,landingPageClicks,reactions,comments,shares,"
        "totalEngagements,approximateUniqueImpressions,"
        "costInLocalCurrency,costInUsd,pivotValues,dateRange"
    )
    url = (
        "https://api.linkedin.com/rest/adAnalytics"
        f"?q=analytics&pivot=CAMPAIGN&timeGranularity=DAILY"
        f"&dateRange={date_range}"
        f"&accounts={_restli_list([f'urn:li:sponsoredAccount:{ad_account_id}'])}"
        f"&fields={fields}"
    )
    resp = await client.get(url, headers=headers)
    if resp.status_code >= 400:
        return {"_error": MetricBundle(notes=f"adAnalytics HTTP {resp.status_code}: {resp.text[:200]}")}

    out: dict[str, MetricBundle] = {}
    for el in (resp.json() or {}).get("elements") or []:
        pivots = el.get("pivotValues") or []
        campaign = next(
            (str(p) for p in pivots if "sponsoredCampaign" in str(p)),
            str(pivots[0]) if pivots else "unknown",
        )
        bundle = out.setdefault(campaign, MetricBundle(raw={"daily": [], "costLocal": 0.0, "costUsd": 0.0}))
        bundle.impressions += int(el.get("impressions") or 0)
        bundle.clicks += int(el.get("clicks") or 0)
        bundle.likes += int(el.get("reactions") or 0)
        bundle.comments += int(el.get("comments") or 0)
        bundle.shares += int(el.get("shares") or 0)
        bundle.reach += int(el.get("approximateUniqueImpressions") or 0)
        bundle.raw["costLocal"] = round(bundle.raw["costLocal"] + float(el.get("costInLocalCurrency") or 0), 2)
        bundle.raw["costUsd"] = round(bundle.raw["costUsd"] + float(el.get("costInUsd") or 0), 2)
        bundle.raw["daily"].append({
            "dateRange": el.get("dateRange"),
            "impressions": el.get("impressions"),
            "clicks": el.get("clicks"),
            "totalEngagements": el.get("totalEngagements"),
            "costInLocalCurrency": el.get("costInLocalCurrency"),
        })
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
    if metrics.notes in ("stats_unavailable", "platform_deleted"):
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
            # Member post stats have no LinkedIn API equivalent (ugcPosts
            # FINDER needs the restricted r_member_social scope). The only
            # discovery path is the browser sidecar's activity-page scrape.
            # LinkedIn conflates urn types — a post may be stored locally as
            # urn:li:share:{id} but scraped as urn:li:activity:{id} — so a
            # numeric-suffix map joins them.
            num_to_post_id: dict[str, uuid.UUID] = {}
            for urn, pid in urn_to_post_id.items():
                tail = urn.rsplit(":", 1)[-1]
                if tail.isdigit():
                    num_to_post_id[tail] = pid
            try:
                from app.services.linkedin_sidecar import LinkedInSidecarClient

                activity = await LinkedInSidecarClient().get_profile_activity()
                for item in activity.get("posts", []):
                    urn = item.get("urn")
                    if not urn:
                        continue
                    tail = urn.rsplit(":", 1)[-1]
                    post_id = urn_to_post_id.get(urn) or num_to_post_id.get(tail)
                    urn_to_post_id.setdefault(urn, post_id)
                    stats_map[urn] = MetricBundle(
                        impressions=int(item.get("impressions") or 0),
                        likes=int(item.get("reactions") or 0),
                        comments=int(item.get("comments") or 0),
                        raw={
                            "scrape": item,
                            "profile_url": activity.get("profile_url"),
                        },
                    )
                followers = activity.get("followers")
                # A logged-out scrape yields 0, not None — persisting it would
                # poison the series (first real count then reads as a huge gain).
                if followers:
                    db.add(FollowerSnapshot(
                        team_id=account.team_id, social_account_id=account.id,
                        platform="linkedin", followers=int(followers),
                    ))
            except Exception as exc:  # noqa: BLE001 — scrape is best-effort
                stats_map.update({
                    urn: MetricBundle(notes="member_stats_not_implemented")
                    for urn in urn_to_post_id
                    if urn not in stats_map
                })
                result.errors.append(f"linkedin member scrape: {exc}")
            all_urns = list(urn_to_post_id.keys())
            for urn in all_urns:
                stats_map.setdefault(
                    urn, MetricBundle(notes="member_stats_not_implemented")
                )

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

        # Paid (Boost) analytics — Advertising Reporting API. Only queried
        # when the token actually carries r_ads_reporting and an ad account
        # is configured; otherwise this is a no-op.
        ad_account_id = (get_settings().LINKEDIN_AD_ACCOUNT_ID or "").strip()
        if is_org and ad_account_id and "r_ads_reporting" in (account.scopes or []):
            ad_stats = await _fetch_linkedin_ad_stats(client, token, ad_account_id, since=since)
            for campaign_urn, ad_metrics in ad_stats.items():
                if campaign_urn == "_error":
                    result.errors.append(ad_metrics.notes or "adAnalytics error")
                    continue
                await _persist_snapshot(
                    db,
                    account=account,
                    post_id=None,
                    platform_post_id=campaign_urn,
                    metrics=ad_metrics,
                    captured_at=captured_at,
                    source="linkedin_ads",
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
            # Mirror into AnalyticsEvent so account-level dashboards see it.
            _persist_account_event(
                db, account, captured_at, "account_insights", {
                    "impressions_lifetime": org_lifetime.impressions,
                    "clicks_lifetime": org_lifetime.clicks,
                    "likes_lifetime": org_lifetime.likes,
                    "comments_lifetime": org_lifetime.comments,
                    "shares_lifetime": org_lifetime.shares,
                    "engagement_lifetime": org_lifetime.engagement,
                },
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
    if resp.status_code in (402, 403):
        # non_public_metrics needs a paid X API tier; free tier gets 402.
        params = {"tweet.fields": "public_metrics"}
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        if resp.status_code in (402, 429):
            # Free-tier read quota exhausted — persistent state, not transient.
            return MetricBundle(notes="quota_exhausted")
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


async def _scrape_twitter_timeline(username: str) -> dict[str, Any]:
    """Scrape an X profile timeline via the shared browser bridge.

    Fallback for when the free API tier has no read credits left. Returns
    {posts: [{id, text, replies, reposts, likes, views, posted}], followers}.
    """
    from app.core.config import get_settings
    from app.services.browser_bridge import BrowserBridgeClient
    from app.services.browser_orchestrator import browser_session

    bridge = BrowserBridgeClient(
        get_settings().BROWSER_BRIDGE_URL, platform="twitter"
    )
    session = await bridge.ensure_session("twitter")
    if session.get("status") != "active":
        return {
            "posts": [],
            "followers": None,
            "error": session.get("message", "browser session not active"),
        }

    extract_js = """(() => {
      const num = s => { if (!s) return 0;
        const m = String(s).replace(/,/g, '').match(/[\\d.]+/); if (!m) return 0;
        let n = parseFloat(m[0]);
        if (/k\\b/i.test(s)) n *= 1e3; if (/m\\b/i.test(s)) n *= 1e6;
        return Math.round(n); };
      const seen = new Set(); const posts = [];
      document.querySelectorAll('article[data-testid="tweet"]').forEach(a => {
        const link = [...a.querySelectorAll('a[href*="/status/"]')]
          .map(x => x.href).find(h => /\\/status\\/\\d+/.test(h));
        const id = link ? (link.match(/\\/status\\/(\\d+)/) || [])[1] : null;
        if (!id || seen.has(id)) return; seen.add(id);
        const pick = sel => { const e = a.querySelector(sel);
          return e ? (e.innerText || e.getAttribute('aria-label') || '') : ''; };
        const t = a.querySelector('time');
        posts.push({
          id,
          text: (a.querySelector('[data-testid="tweetText"]') || {}).innerText
            ? (a.querySelector('[data-testid="tweetText"]').innerText || '').slice(0, 280) : '',
          replies: num(pick('[data-testid="reply"]')),
          reposts: num(pick('[data-testid="retweet"]')),
          likes: num(pick('[data-testid="like"]')),
          views: num(pick('a[href*="/analytics"] [data-testid="app-text-transition-container"], [aria-label*="view"]')),
          posted: t ? t.getAttribute('datetime') : null,
        });
      });
      const fm = document.body.innerText.match(/([\\d,.]+[KkMm]?)\\s*Followers/);
      return { posts, followers: fm ? num(fm[1]) : null };
    })()"""

    async with browser_session("twitter", bridge) as b:
        await b.navigate(f"https://x.com/{username}")
        for _ in range(4):
            try:
                await b.evaluate("window.scrollBy(0, 1400)")
            except Exception:  # noqa: BLE001 — scroll is best-effort
                pass
            await asyncio.sleep(1)
        result = await b.evaluate(extract_js)
    data = result.get("result") if isinstance(result, dict) else result
    return data if isinstance(data, dict) else {"posts": [], "followers": None}


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

    # Discover tweets SocialAuto didn't publish (native X posts) via the
    # authenticated user's timeline — same backfill pattern as TikTok.
    user_id = account.account_id
    discovered: dict[str, uuid.UUID | None] = {}
    id_to_post: dict[str, uuid.UUID] = {}
    for t in targets:
        tid = (t.platform_post_id or "").split("/")[-1]
        if tid:
            id_to_post[tid] = t.post_id

    async with httpx.AsyncClient(timeout=30.0) as client:
        covered: set[str] = set()
        for t in targets:
            tweet_id = (t.platform_post_id or "").split("/")[-1]
            if not tweet_id:
                continue
            covered.add(tweet_id)
            metrics = await _fetch_twitter_metrics(client, token, tweet_id)
            if metrics.notes == "quota_exhausted":
                # Free tier is out of read credits — every remaining call will
                # 402 identically. Record one data-gap marker and move on.
                metrics = MetricBundle(notes="quota_exhausted")
                await _persist_snapshot(
                    db, account=account, post_id=t.post_id, platform_post_id=tweet_id,
                    metrics=metrics, captured_at=captured_at, source="twitter_api",
                    result=result, platform="twitter",
                )
                result.skipped += len(targets) - len(covered)
                break
            await _persist_snapshot(
                db, account=account, post_id=t.post_id, platform_post_id=tweet_id,
                metrics=metrics, captured_at=captured_at, source="twitter_api",
                result=result, platform="twitter",
            )

        # User timeline discovery — Free/Basic tiers reject with 402/403.
        since_ts = since.timestamp()
        try:
            resp = await client.get(
                f"https://api.x.com/2/users/{user_id}/tweets",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "tweet.fields": "public_metrics,created_at",
                    "max_results": "50",
                    "exclude": "replies,retweets",
                },
            )
            if resp.status_code == 200:
                for tw in (resp.json() or {}).get("data", []):
                    tid = tw.get("id")
                    if not tid or tid in covered:
                        continue
                    try:
                        created = datetime.fromisoformat(
                            tw["created_at"].replace("Z", "+00:00")
                        ).timestamp() if tw.get("created_at") else None
                    except ValueError:
                        created = None
                    if created and created < since_ts:
                        continue
                    pm = tw.get("public_metrics", {}) or {}
                    metrics = MetricBundle(
                        impressions=int(pm.get("impression_count", 0) or 0),
                        likes=int(pm.get("like_count", 0) or 0),
                        comments=int(pm.get("reply_count", 0) or 0),
                        shares=int(pm.get("retweet_count", 0) or 0)
                        + int(pm.get("quote_count", 0) or 0),
                        reach=int(pm.get("impression_count", 0) or 0),
                        raw=tw,
                    )
                    await _persist_snapshot(
                        db, account=account, post_id=id_to_post.get(tid),
                        platform_post_id=tid, metrics=metrics,
                        captured_at=captured_at, source="twitter_api",
                        result=result, platform="twitter",
                    )
                    discovered[tid] = id_to_post.get(tid)
            elif resp.status_code in (401, 402, 403, 429):
                result.errors.append(
                    f"twitter timeline discovery HTTP {resp.status_code} "
                    "(needs paid tier for read access)"
                )
            else:
                result.errors.append(
                    f"twitter timeline discovery HTTP {resp.status_code}"
                )
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"twitter timeline discovery: {exc}")

        # Browser-scrape fallback — the free X API tier has no timeline read
        # credits, so discovery of native tweets goes through the shared
        # browser bridge when the API refuses.
        if not discovered:
            try:
                scraped = await _scrape_twitter_timeline(str(account.username or ""))
                for tw in scraped.get("posts", []):
                    tid = tw.get("id")
                    if not tid or tid in covered:
                        continue
                    try:
                        created = datetime.fromisoformat(
                            tw["posted"].replace("Z", "+00:00")
                        ).timestamp() if tw.get("posted") else None
                    except (ValueError, AttributeError):
                        created = None
                    if created and created < since_ts:
                        continue
                    metrics = MetricBundle(
                        impressions=int(tw.get("views") or 0),
                        likes=int(tw.get("likes") or 0),
                        comments=int(tw.get("replies") or 0),
                        shares=int(tw.get("reposts") or 0),
                        reach=int(tw.get("views") or 0),
                        raw=tw,
                    )
                    await _persist_snapshot(
                        db, account=account, post_id=id_to_post.get(tid),
                        platform_post_id=tid, metrics=metrics,
                        captured_at=captured_at, source="twitter_scrape",
                        result=result, platform="twitter",
                    )
                    discovered[tid] = id_to_post.get(tid)
                followers = scraped.get("followers")
                # Same guard as the LinkedIn scrape — 0 means the scrape failed.
                if followers:
                    db.add(FollowerSnapshot(
                        team_id=account.team_id, social_account_id=account.id,
                        platform="twitter", followers=int(followers),
                    ))
            except Exception as exc:  # noqa: BLE001 — scrape is best-effort
                result.errors.append(f"twitter timeline scrape: {exc}")

        # Account-level metrics — followers/following/tweet_count via
        # users/me public_metrics (free tier). A 401 means the stored
        # OAuth2 user token is dead — recorded once, not per tweet.
        try:
            ur = await client.get(
                "https://api.x.com/2/users/me",
                params={"user.fields": "public_metrics"},
                headers={"Authorization": f"Bearer {token}"},
            )
            if ur.status_code == 200:
                pm = ((ur.json() or {}).get("data") or {}).get("public_metrics") or {}
                if pm:
                    _persist_account_event(
                        db, account, captured_at, "account_insights",
                        {
                            "followers_count": int(pm.get("followers_count") or 0),
                            "following_count": int(pm.get("following_count") or 0),
                            "tweet_count": int(pm.get("tweet_count") or 0),
                            "listed_count": int(pm.get("listed_count") or 0),
                        },
                    )
            else:
                result.errors.append(
                    f"twitter users/me HTTP {ur.status_code}: {ur.text[:150]}"
                )
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"twitter users/me: {exc}")

    if result.synced == 0:
        result.skipped = len(targets)
    await db.commit()
    return result


# ── Facebook ──────────────────────────────────────────────────────────────────

async def _fetch_facebook_post_metrics(
    client: httpx.AsyncClient, page_token: str, post_id: str,
) -> MetricBundle:
    """Fetch insights for a Facebook page post via Graph API.

    Meta removed post_impressions/post_comments/post_shares from post
    insights in v26 (June 2026). Remaining insight metrics: post_media_view
    (impressions replacement), post_clicks, post_reactions_like_total,
    post_reactions_by_type_total, post_activity_by_action_type,
    post_video_views. Comments/shares come from the post object itself
    (comments.summary.total_count, shares.count).
    """
    url = facebook_graph_url(f"{post_id}/insights")
    resp = await _meta_insights_get(
        client,
        url,
        [
            "post_media_view", "post_clicks", "post_reactions_like_total",
            "post_reactions_by_type_total", "post_activity_by_action_type",
            "post_video_views",
        ],
        params={"access_token": page_token},
    )
    if resp.status_code != 200:
        return MetricBundle(
            notes=f"facebook stats HTTP {resp.status_code}: {_meta_error_message(resp)}"
        )
    data = resp.json() or {}
    raw_metrics = {item["name"]: item for item in data.get("data", [])}

    def _val(name: str, idx: int = 0) -> int:
        item = raw_metrics.get(name)
        if not item:
            return 0
        values = item.get("values", [])
        if idx < len(values):
            value = values[idx].get("value", 0)
            return int(value) if isinstance(value, int | float) else 0
        return 0

    def _breakdown(name: str, key: str) -> int:
        item = raw_metrics.get(name)
        if not item:
            return 0
        for v in item.get("values", []):
            value = v.get("value")
            if isinstance(value, dict):
                return int(value.get(key, 0) or 0)
        return 0

    impressions = _val("post_media_view") or _val("post_video_views")

    # Comments/shares removed from post insights — read them off the object.
    comments = _breakdown("post_activity_by_action_type", "comment")
    shares = _breakdown("post_activity_by_action_type", "share")
    obj = await client.get(
        facebook_graph_url(post_id),
        params={
            "access_token": page_token,
            "fields": "shares,comments.summary(true).limit(0)",
        },
    )
    if obj.status_code == 200:
        body = obj.json() or {}
        comments = comments or int(
            (body.get("comments", {}).get("summary", {}) or {}).get("total_count", 0) or 0
        )
        shares = shares or int((body.get("shares", {}) or {}).get("count", 0) or 0)

    return MetricBundle(
        impressions=impressions,
        clicks=_val("post_clicks"),
        likes=_val("post_reactions_like_total"),
        comments=comments,
        shares=shares,
        reach=impressions,
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

    # Prefer stored page_token from meta_data (avoids extra API call)
    meta = account.meta_data or {}
    page_token = meta.get("page_token") or token
    async with httpx.AsyncClient(timeout=30.0) as client:
        if not meta.get("page_token"):
            resp = await client.get(
                facebook_graph_url("me/accounts"),
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
        id_to_post = {t.platform_post_id: t.post_id for t in targets if t.platform_post_id}

        covered: set[str] = set()
        for t in targets:
            fb_post_id = t.platform_post_id or ""
            if not fb_post_id:
                continue
            covered.add(fb_post_id)
            metrics = await _fetch_facebook_post_metrics(client, page_token, fb_post_id)
            await _persist_snapshot(
                db, account=account, post_id=t.post_id, platform_post_id=fb_post_id,
                metrics=metrics, captured_at=captured_at, source="facebook_api",
                result=result, platform="facebook",
            )

        # Discover posts SocialAuto didn't publish. `published_posts` is a
        # Page-only edge — personal (user) accounts must use `feed`.
        discovery_edge = (
            "published_posts" if account.account_type == "page" else "feed"
        )
        try:
            resp = await client.get(
                facebook_graph_url(f"{page_id}/{discovery_edge}"),
                params={
                    "access_token": page_token,
                    "fields": "id,created_time,message",
                    "limit": "50",
                },
            )
            if resp.status_code == 200:
                for item in (resp.json() or {}).get("data", []):
                    pid = item.get("id")
                    if not pid or pid in covered:
                        continue
                    try:
                        created = datetime.fromisoformat(
                            item["created_time"].replace("Z", "+00:00")
                        ) if item.get("created_time") else None
                    except ValueError:
                        created = None
                    if created and created < since:
                        continue
                    metrics = await _fetch_facebook_post_metrics(client, page_token, pid)
                    metrics.raw = {**(metrics.raw or {}), "discovery": item}
                    await _persist_snapshot(
                        db, account=account, post_id=id_to_post.get(pid),
                        platform_post_id=pid, metrics=metrics,
                        captured_at=captured_at, source="facebook_api",
                        result=result, platform="facebook",
                    )
            else:
                result.errors.append(
                    f"facebook {discovery_edge} HTTP {resp.status_code}: {_meta_error_message(resp)}"
                )
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"facebook post discovery: {exc}")

        # Page-level insights — Pages only (meta.page_token present).
        # v26-valid metrics: Meta removed page_impressions, page_fans,
        # page_engaged_users, page_impressions_unique, page_fan_adds.
        if meta.get("page_token"):
            try:
                resp = await _meta_insights_get(
                    client,
                    facebook_graph_url(f"{page_id}/insights"),
                    [
                        "page_views_total", "page_post_engagements",
                        "page_daily_follows", "page_follows",
                        "page_total_actions", "page_video_views",
                    ],
                    params={"access_token": page_token, "period": "day"},
                )
                if resp.status_code == 200:
                    agg: dict[str, int] = {}
                    for item in (resp.json() or {}).get("data", []):
                        vals = item.get("values") or []
                        latest = vals[-1].get("value", 0) if vals else 0
                        agg[item["name"]] = int(latest) if isinstance(latest, int | float) else 0
                    if agg:
                        _persist_account_event(
                            db, account, captured_at, "account_insights", agg
                        )
                else:
                    result.errors.append(
                        f"facebook page insights HTTP {resp.status_code}: "
                        f"{_meta_error_message(resp)}"
                    )
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"facebook page insights: {exc}")

    if result.synced == 0:
        result.skipped = len(targets)
    await db.commit()
    return result


# ── Instagram ─────────────────────────────────────────────────────────────────

async def _fetch_instagram_media_metrics(
    client: httpx.AsyncClient, token: str, ig_user_id: str, media_id: str,
) -> MetricBundle:
    """Fetch insights for an Instagram media post via Graph API.

    Meta deprecated `impressions` for media insights in v22.0 (April 2025).
    `views` is the replacement metric. `likes`, `comments`, `saves` remain.
    Uses graph.instagram.com for Instagram Login tokens (IGAAU* prefix),
    graph.facebook.com for Facebook Login tokens.
    """
    # Instagram Login tokens (IGAAU*) need graph.instagram.com host;
    # Facebook Login tokens use graph.facebook.com.
    if token.startswith("IGAAU"):
        url = f"https://graph.instagram.com/v26.0/{media_id}/insights"
    else:
        url = facebook_graph_url(f"{media_id}/insights")
    # FEED/REELS-safe metrics first. Meta rejects `impressions` and `replies`
    # for most media product types (FEED/IMAGE/VIDEO/CAROUSEL); `replies` is
    # Stories-only. Adaptive drop still handles version/token renames.
    # `saved` is the valid name — `saves` is rejected with metric[N] by Meta.
    resp = await _meta_insights_get(
        client,
        url,
        ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions"],
        params={"access_token": token},
    )
    if resp.status_code != 200:
        return MetricBundle(
            notes=f"instagram stats HTTP {resp.status_code}: {_meta_error_message(resp)}"
        )
    data = resp.json() or {}
    raw_metrics = {item["name"]: item for item in data.get("data", [])}

    def _val(name: str) -> int:
        item = raw_metrics.get(name)
        if not item:
            return 0
        values = item.get("values", [])
        return int(values[0].get("value", 0) or 0) if values else 0

    impressions = _val("views") or _val("impressions")
    return MetricBundle(
        impressions=impressions,
        likes=_val("likes"),
        comments=_val("comments"),
        shares=_val("shares") or _val("replies"),
        reach=_val("reach") or impressions,
        raw=data,
    )


async def sync_instagram_account(
    db: AsyncSession,
    account: SocialAccount,
    *,
    days: int = 365,
) -> SyncResult:
    result = SyncResult()

    # Personal Instagram accounts don't have Graph API insights access.
    # Only Business/Creator accounts can use the /insights endpoint.
    meta = account.meta_data or {}
    account_type = meta.get("account_type", "person")
    if account_type not in ("business", "creator", "BUSINESS", "CREATOR"):
        result.skipped = 1
        result.notes = "personal account — Graph API insights require Business/Creator account"
        return result

    token = decrypt_token(account.access_token_enc)
    ig_user_id = account.account_id
    since = datetime.now(UTC) - timedelta(days=days)
    captured_at = datetime.now(UTC)

    # Tokens granted without an insights scope 403 on every media call —
    # skip the API entirely and record one clear note instead of an
    # error storm per media item.
    stored_scopes = set(account.scopes or meta.get("scopes") or [])
    insights_scopes = {
        "instagram_business_manage_insights",
        "instagram_manage_insights",
    }
    insights_allowed = (
        not stored_scopes or bool(stored_scopes & insights_scopes)
    )

    def _insights_or_skip(media_id: str) -> MetricBundle | None:
        if insights_allowed:
            return None
        return MetricBundle(
            notes="insights_scope_missing — reconnect account with "
                  "instagram_business_manage_insights"
        )

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
    id_to_post = {t.platform_post_id: t.post_id for t in targets if t.platform_post_id}

    ig_host = "https://graph.instagram.com/v26.0" if token.startswith("IGAAU") else None

    async with httpx.AsyncClient(timeout=30.0) as client:
        covered: set[str] = set()
        for t in targets:
            media_id = t.platform_post_id or ""
            if not media_id:
                continue
            covered.add(media_id)
            metrics = _insights_or_skip(media_id) or await _fetch_instagram_media_metrics(
                client, token, ig_user_id, media_id
            )
            await _persist_snapshot(
                db, account=account, post_id=t.post_id, platform_post_id=media_id,
                metrics=metrics, captured_at=captured_at, source="instagram_api",
                result=result, platform="instagram",
            )

        # Discover IG media SocialAuto didn't publish (native app posts).
        try:
            media_url = (
                f"{ig_host}/{ig_user_id}/media" if ig_host
                else facebook_graph_url(f"{ig_user_id}/media")
            )
            resp = await client.get(
                media_url,
                params={
                    "access_token": token,
                    "fields": "id,timestamp,caption,media_type,permalink",
                    "limit": "50",
                },
            )
            if resp.status_code == 200:
                for item in (resp.json() or {}).get("data", []):
                    mid = item.get("id")
                    if not mid or mid in covered:
                        continue
                    try:
                        created = datetime.fromisoformat(
                            item["timestamp"].replace("Z", "+00:00")
                        ) if item.get("timestamp") else None
                    except ValueError:
                        created = None
                    if created and created < since:
                        continue
                    metrics = _insights_or_skip(mid) or await _fetch_instagram_media_metrics(
                        client, token, ig_user_id, mid
                    )
                    metrics.raw = {**(metrics.raw or {}), "discovery": item}
                    await _persist_snapshot(
                        db, account=account, post_id=id_to_post.get(mid),
                        platform_post_id=mid, metrics=metrics,
                        captured_at=captured_at, source="instagram_api",
                        result=result, platform="instagram",
                    )
            else:
                result.errors.append(
                    f"instagram media discovery HTTP {resp.status_code}: {_meta_error_message(resp)}"
                )
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"instagram media discovery: {exc}")

        # Account-level insights + audience demographics — requires an
        # insights scope on the token (skip quietly when not granted).
        if insights_allowed:
            insights_base = (
                f"{ig_host}/{ig_user_id}/insights" if ig_host
                else facebook_graph_url(f"{ig_user_id}/insights")
            )
            try:
                resp = await _meta_insights_get(
                    client,
                    insights_base,
                    ["reach", "follower_count", "profile_views",
                     "accounts_engaged", "total_interactions"],
                    params={"access_token": token, "period": "day"},
                )
                if resp.status_code == 200:
                    agg: dict[str, int] = {}
                    for item in (resp.json() or {}).get("data", []):
                        vals = item.get("values") or []
                        latest = vals[-1].get("value", 0) if vals else 0
                        agg[item["name"]] = int(latest) if isinstance(latest, int | float) else 0
                    if agg:
                        _persist_account_event(
                            db, account, captured_at, "account_insights", agg
                        )
                else:
                    result.errors.append(
                        f"instagram account insights HTTP {resp.status_code}: "
                        f"{_meta_error_message(resp)}"
                    )
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"instagram account insights: {exc}")

            # Audience demographics (country/age/gender) — campaign targeting data.
            try:
                resp = await client.get(
                    insights_base,
                    params={
                        "access_token": token,
                        "metric": "follower_demographics",
                        "period": "lifetime",
                        "metric_type": "total_value",
                        "timeframe": "this_month",
                        "breakdown": "country",
                    },
                )
                if resp.status_code == 200:
                    # Response: data[0].total_value.breakdowns[0].results[]
                    # each result = {"dimension_values": ["GR"], "value": n}
                    by_country: dict[str, int] = {}
                    for item in (resp.json() or {}).get("data", []):
                        for bd in (item.get("total_value") or {}).get("breakdowns") or []:
                            for r in bd.get("results") or []:
                                key = str((r.get("dimension_values") or ["?"])[0])
                                by_country[key] = int(r.get("value", 0) or 0)
                    if by_country:
                        _persist_account_event(
                            db, account, captured_at, "audience_demographics",
                            {"breakdown": "country", "by_country": by_country},
                        )
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"instagram demographics: {exc}")

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
    headers = {"Authorization": f"Bearer {token}"}
    resp = await _meta_insights_get(
        client,
        url,
        ["views", "likes", "replies", "reposts", "quotes"],
        headers=headers,
    )
    if resp.status_code != 200:
        msg = _meta_error_message(resp)
        if "does not exist" in msg:
            return MetricBundle(notes="platform_deleted")
        return MetricBundle(
            notes=f"threads stats HTTP {resp.status_code}: {msg}"
        )
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


async def _fetch_threads_account_insights(
    client: httpx.AsyncClient, token: str, user_id: str,
) -> dict[str, Any]:
    """Fetch account-level insights from the Threads API.

    Returns a dict with aggregated views, likes, replies, reposts, quotes
    across all posts in the default 30-day window the API returns.
    """
    url = f"https://graph.threads.net/v1.0/{user_id}/threads_insights"
    resp = await _meta_insights_get(
        client,
        url,
        ["views", "likes", "replies", "reposts", "quotes", "followers_count"],
        params={"access_token": token},
    )
    if resp.status_code != 200:
        return {}
    data = resp.json() or {}
    aggregated: dict[str, int] = {}
    for item in data.get("data", []):
        name = item.get("name", "")
        values = item.get("values", [])
        total = sum(int(v.get("value", 0) or 0) for v in values) if values else 0
        aggregated[name] = total
    return aggregated


async def _fetch_threads_profile(
    client: httpx.AsyncClient, token: str, user_id: str,
) -> dict[str, Any]:
    """Fetch the Threads profile.

    The Threads user node does not support followers_count, following_count,
    or media_count as fields (returns 500). Only request supported fields.
    Follower/reach metrics are fetched separately via the /insights endpoint.
    """
    url = f"https://graph.threads.net/v1.0/{user_id}"
    params = {
        "fields": "username,name,threads_profile_picture_url,threads_biography,is_verified",
        "access_token": token,
    }
    resp = await client.get(url, params=params)
    if resp.status_code != 200:
        return {}
    return resp.json() or {}


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
    id_to_post = {t.platform_post_id: t.post_id for t in targets if t.platform_post_id}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Per-post media metrics
        covered: set[str] = set()
        for t in targets:
            media_id = t.platform_post_id or ""
            if not media_id:
                continue
            covered.add(media_id)
            metrics = await _fetch_threads_media_metrics(client, token, media_id)
            if metrics.notes == "platform_deleted":
                # Media no longer exists on Threads (deleted upstream) —
                # retire the target so future syncs skip it.
                t.status = "deleted"
                t.error_message = "Media deleted on Threads"
                result.skipped += 1
                continue
            await _persist_snapshot(
                db, account=account, post_id=t.post_id, platform_post_id=media_id,
                metrics=metrics, captured_at=captured_at, source="threads_api",
                result=result, platform="threads",
            )

        # Discover threads SocialAuto didn't publish (native Threads posts).
        try:
            resp = await client.get(
                f"https://graph.threads.net/v1.0/{account.account_id}/threads",
                params={
                    "access_token": token,
                    "fields": "id,timestamp,text,media_type,permalink",
                    "limit": "50",
                },
            )
            if resp.status_code == 200:
                for item in (resp.json() or {}).get("data", []):
                    mid = item.get("id")
                    if not mid or mid in covered:
                        continue
                    ts = item.get("timestamp")
                    try:
                        created = (
                            datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                            if ts else None
                        )
                    except (ValueError, TypeError):
                        created = None
                    if created and created < since:
                        continue
                    metrics = await _fetch_threads_media_metrics(client, token, mid)
                    metrics.raw = {**(metrics.raw or {}), "discovery": item}
                    await _persist_snapshot(
                        db, account=account, post_id=id_to_post.get(mid),
                        platform_post_id=mid, metrics=metrics,
                        captured_at=captured_at, source="threads_api",
                        result=result, platform="threads",
                    )
            else:
                result.errors.append(
                    f"threads discovery HTTP {resp.status_code}: {_meta_error_message(resp)}"
                )
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"threads post discovery: {exc}")

        # Account-level insights (aggregated views, likes, replies, reposts, quotes)
        try:
            account_insights = await _fetch_threads_account_insights(client, token, account.account_id)
            if account_insights:
                # Persist as an AnalyticsEvent for dashboard aggregates
                event = AnalyticsEvent(
                    team_id=account.team_id,
                    social_account_id=account.id,
                    platform="threads",
                    event_type="account_insights",
                    occurred_at=captured_at,
                    meta_data={
                        "captured_at": captured_at.isoformat(),
                        "views": account_insights.get("views", 0),
                        "likes": account_insights.get("likes", 0),
                        "replies": account_insights.get("replies", 0),
                        "reposts": account_insights.get("reposts", 0),
                        "quotes": account_insights.get("quotes", 0),
                        "followers_count": account_insights.get("followers_count", 0),
                    },
                )
                db.add(event)
        except Exception as exc:
            result.errors.append(f"threads account insights: {exc}")

        # Profile data (followers_count, following_count, media_count)
        try:
            profile = await _fetch_threads_profile(client, token, account.account_id)
            if profile:
                # Update account metadata from profile
                if profile.get("username") and not account.username:
                    account.username = profile["username"]
                if profile.get("name") and not account.display_name:
                    account.display_name = profile["name"]
                if profile.get("threads_profile_picture_url") and not account.avatar_url:
                    account.avatar_url = profile["threads_profile_picture_url"]
                # Persist profile metadata for dashboard use
                event = AnalyticsEvent(
                    team_id=account.team_id,
                    social_account_id=account.id,
                    platform="threads",
                    event_type="profile_sync",
                    occurred_at=captured_at,
                    meta_data={
                        "captured_at": captured_at.isoformat(),
                        "followers_count": int(profile.get("followers_count", 0) or 0),
                        "following_count": int(profile.get("following_count", 0) or 0),
                        "media_count": int(profile.get("media_count", 0) or 0),
                        "username": profile.get("username", ""),
                    },
                )
                db.add(event)
        except Exception as exc:
            result.errors.append(f"threads profile sync: {exc}")

    if result.synced == 0:
        result.skipped = len(targets)
    await db.commit()
    return result


# ── TikTok ────────────────────────────────────────────────────────────────────

def _resolve_tiktok_display_video_id(target: PostTarget) -> str | None:
    """Return a Display API video id suitable for analytics, or None to skip.

    Direct Post success stores ``publicaly_available_post_id`` as
    ``platform_post_id`` (and in ``platform_specific.tiktok``). Inbox
    MEDIA_UPLOAD keeps the Content Posting ``publish_id`` until the creator
    finishes in TikTok — those cannot be queried via Display API.
    """
    from app.services.tiktok_api import is_tiktok_publish_id

    ps = {}
    if target.post is not None:
        ps = (target.post.platform_specific or {}).get("tiktok") or {}
    public = ps.get("publicaly_available_post_id") or ps.get("public_post_id")
    if public:
        return str(public)
    video_id = (target.platform_post_id or "").strip()
    if not video_id:
        return None
    if is_tiktok_publish_id(video_id):
        return None
    return video_id


async def _fetch_tiktok_video_stats(
    client: httpx.AsyncClient, token: str, video_id: str,
) -> MetricBundle:
    """Fetch stats for a TikTok video via Display API ``POST /v2/video/query/``.

    Docs: https://developers.tiktok.com/doc/tiktok-api-v2-video-query
    Requires ``video.list`` scope. Fields mirror the official Video Object
    (no ``reach_count`` — that field is not documented for Display API).
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    fields = "id,view_count,like_count,comment_count,share_count,share_url,title"
    url = "https://open.tiktokapis.com/v2/video/query/"
    resp = await client.post(
        url,
        headers=headers,
        params={"fields": fields},
        json={"filters": {"video_ids": [video_id]}},
    )
    if resp.status_code != 200:
        return MetricBundle(notes=f"tiktok stats HTTP {resp.status_code}")
    data = resp.json() or {}
    error = data.get("error") or {}
    if error.get("code") not in (None, "", "ok"):
        return MetricBundle(
            notes=f"tiktok stats error: {error.get('code')} {error.get('message')}"
        )
    videos = (data.get("data") or {}).get("videos", [])
    for v in videos:
        if str(v.get("id")) == str(video_id):
            views = int(v.get("view_count", 0) or 0)
            return MetricBundle(
                impressions=views,
                likes=int(v.get("like_count", 0) or 0),
                comments=int(v.get("comment_count", 0) or 0),
                shares=int(v.get("share_count", 0) or 0),
                reach=views,
                raw=v,
            )
    return MetricBundle(notes="tiktok_video_not_found")


def _write_tiktok_cookie_file(cookies: dict[str, str]) -> str:
    """Build a Netscape cookies.txt from a name→value map; returns the path."""
    import tempfile
    import time

    exp = int(time.time()) + 86400 * 180
    lines = ["# Netscape HTTP Cookie File"]
    for name, value in cookies.items():
        lines.append(f".tiktok.com\tTRUE\t/\tTRUE\t{exp}\t{name}\t{value}")
    fd, path = tempfile.mkstemp(prefix="tt_cookies_", suffix=".txt")
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _scrape_tiktok_profile(username: str, cookies: dict[str, str]) -> dict[str, Any]:
    """Synchronous yt-dlp extraction of a profile's video grid + stats.

    Runs in a worker thread — yt_dlp is blocking. ``extract_flat`` returns
    per-video stats inline (view/like/comment/share/save counts) so a single
    pass covers every public video on the profile.
    """
    import yt_dlp

    cookie_path = _write_tiktok_cookie_file(cookies) if cookies else None
    try:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
        }
        if cookie_path:
            opts["cookiefile"] = cookie_path
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(
                f"https://www.tiktok.com/@{username}", download=False
            ) or {}
        videos: dict[str, MetricBundle] = {}
        for e in info.get("entries") or []:
            vid = str(e.get("id") or "")
            if not vid:
                continue
            views = int(e.get("view_count") or 0)
            videos[vid] = MetricBundle(
                impressions=views,
                likes=int(e.get("like_count") or 0),
                comments=int(e.get("comment_count") or 0),
                shares=int(e.get("repost_count") or 0),
                reach=views,
                raw={
                    "id": vid,
                    "title": e.get("title") or "",
                    "timestamp": e.get("timestamp"),
                    "duration": e.get("duration"),
                    "save_count": e.get("save_count"),
                    "track": e.get("track"),
                    "artists": e.get("artists"),
                    "uploader": e.get("uploader"),
                },
            )
        return {
            "videos": videos,
            "followers": info.get("channel_follower_count"),
            "channel_id": info.get("channel_id") or info.get("uploader_id"),
        }
    finally:
        if cookie_path:
            try:
                os.unlink(cookie_path)
            except OSError:
                pass


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

    # Map every known video id → post_id so scraped rows can be joined back.
    id_to_post: dict[str, uuid.UUID] = {}
    for t in targets:
        vid = _resolve_tiktok_display_video_id(t)
        if vid:
            id_to_post[vid] = t.post_id
        ps = (t.post.platform_specific or {}).get("tiktok") or {} if t.post else {}
        for k in ("publicaly_available_post_id", "public_post_id", "video_id"):
            if ps.get(k):
                id_to_post[str(ps[k])] = t.post_id

    api_video_ids: set[str] = set()
    if token:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for t in targets:
                video_id = _resolve_tiktok_display_video_id(t)
                if not video_id:
                    result.skipped += 1
                    # Inbox drafts awaiting manual finish carry a documented
                    # error_message — not a sync error, so don't re-report it.
                    if "Draft delivered to TikTok app inbox" not in (t.error_message or ""):
                        result.errors.append(
                            f"tiktok skip post={t.post_id}: no Display video id "
                            f"(inbox publish_id={t.platform_post_id})"
                        )
                    continue
                api_video_ids.add(video_id)
                metrics = await _fetch_tiktok_video_stats(client, token, video_id)
                await _persist_snapshot(
                    db, account=account, post_id=t.post_id, platform_post_id=video_id,
                    metrics=metrics, captured_at=captured_at, source="tiktok_api",
                    result=result, platform="tiktok",
                )

    # yt-dlp scrape of the profile grid — the only source covering
    # MEDIA_UPLOAD inbox posts and videos published straight from the phone.
    username = (account.username or "").lstrip("@")
    cookies = (account.meta_data or {}).get("tiktok_web_cookies") or {}
    if username and cookies:
        try:
            scraped = await asyncio.to_thread(
                _scrape_tiktok_profile, username, cookies
            )
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"tiktok scrape @{username}: {exc}")
            scraped = None
        if scraped:
            for vid, metrics in scraped["videos"].items():
                if vid in api_video_ids:
                    continue  # Display API already persisted the authoritative row
                await _persist_snapshot(
                    db, account=account,
                    post_id=id_to_post.get(vid),
                    platform_post_id=vid,
                    metrics=metrics, captured_at=captured_at,
                    source="tiktok_scrape", result=result, platform="tiktok",
                )
            # Account-level profile stats from the scrape (follower count,
            # channel id, video grid size) — persisted as an event so
            # dashboards can chart it without re-scraping.
            _persist_account_event(
                db, account, captured_at, "profile_sync",
                {
                    "followers_count": scraped.get("followers") or 0,
                    "channel_id": scraped.get("channel_id") or "",
                    "video_count": len(scraped.get("videos") or {}),
                },
            )
            if not scraped["videos"]:
                # 0 scraped videos with cookies present could mean a dead
                # web session rather than an empty profile — ask the sidecar.
                try:
                    from app.core.config import get_settings

                    async with httpx.AsyncClient(timeout=30.0) as sc:
                        sess = await sc.get(
                            f"{get_settings().TIKTOK_BROWSER_SIDECAR_URL}/session"
                        )
                    if sess.status_code == 200 and not sess.json().get("logged_in"):
                        result.errors.append(
                            f"tiktok scrape @{username}: web session expired — "
                            f"re-run QR login (see tiktok-console-ops skill)"
                        )
                except Exception:  # noqa: BLE001
                    pass

    if result.synced == 0 and result.skipped == 0:
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
                # Messaging-only platforms (whatsapp, telegram, …) have no
                # analytics surface — skip quietly instead of re-reporting.
                combined.skipped += 1
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
        if followers <= 0:
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
