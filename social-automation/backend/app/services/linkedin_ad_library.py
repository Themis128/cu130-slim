"""LinkedIn Ad Library — public ad-search API for intel (own + competitors).

Provisioned on 264925843 + 265077368 (Default Tier). Pulls ads by
advertiser/keyword into ``linkedin_ad_intel`` via the weekly
``linkedin-ad-library-watch`` beat task.

API quirks (verified 2026-11-06):
- Requires an ACTIVE monthly ``Linkedin-Version`` header — 202608/09/10
  return 200; older monthly versions return 426 NONEXISTENT_VERSION.
- ``X-Restli-Protocol-Version: 2.0.0`` is required.
- Finder ``q=criteria``; paginate with ``start``/``count`` and ~5s between
  pages (documented-safe cadence).
"""

import logging
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_token
from app.models.linkedin_intel import LinkedInAdIntel, LinkedInIntelWatch
from app.models.social_account import SocialAccount
from app.worker._async import task_session

logger = logging.getLogger(__name__)

REST = "https://api.linkedin.com/rest"
HDRS = {"Linkedin-Version": "202610", "X-Restli-Protocol-Version": "2.0.0"}
PAGE_DELAY_S = 5.0
MAX_PAGES = 10
PAGE_SIZE = 50
# Ads absent from results this many consecutive runs flip to "ended".
MISS_THRESHOLD = 2

# Seed watchlist — SMB/cloud-relevant niches for cloudless.gr.
DEFAULT_WATCHES = [
    ("keyword", "cloud migration", "GR"),
    ("keyword", "server costs", "GR"),
    ("keyword", "digital transformation", "GR"),
    ("keyword", "cloud computing", "US"),
    ("advertiser", "amazon web services", "US"),
    ("advertiser", "google cloud", "US"),
    ("advertiser", "microsoft azure", "US"),
]


async def _token() -> str | None:
    async with task_session() as db:
        acc = (
            (
                await db.execute(
                    select(SocialAccount).where(
                        SocialAccount.platform == "linkedin",
                        SocialAccount.account_type == "organization",
                    )
                )
            )
            .scalars()
            .first()
        )
        return decrypt_token(bytes(acc.access_token_enc)) if acc and acc.access_token_enc else None


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **HDRS}


def _summarize(el: dict, keyword: str) -> dict | None:
    """Flatten one adLibrary element into LinkedInAdIntel fields."""
    ad_url = el.get("adUrl")
    if not ad_url:
        return None
    details = el.get("details") or {}
    adv = details.get("advertiser") or {}
    stats = details.get("adStatistics") or {}
    targeting = details.get("adTargeting") or []
    facets = {
        t.get("facetName"): {
            "included": t.get("includedSegments") or [],
            "excluded": t.get("excludedSegments") or [],
        }
        for t in targeting
        if isinstance(t, dict)
    }
    impressions = stats.get("totalImpressions") or {}
    return {
        "ad_url": ad_url[:500],
        "advertiser": (adv.get("advertiserName") or "")[:300],
        "ad_payer": (adv.get("adPayer") or "")[:300],
        "advertiser_url": (adv.get("advertiserUrl") or "")[:500],
        "is_restricted": bool(el.get("isRestricted")),
        "impressions_from": int(impressions.get("from") or impressions.get("lowerBound") or 0),
        "impressions_to": int(impressions.get("to") or impressions.get("upperBound") or 0),
        "first_impression_at": stats.get("firstImpressionAt"),
        "latest_impression_at": stats.get("latestImpressionAt"),
        "countries": stats.get("impressionsDistributionByCountry") or [],
        "targeting": facets,
        "last_keyword": keyword[:300],
        "raw": el,
    }


async def search_page(
    client: httpx.AsyncClient,
    token: str,
    *,
    keyword: str | None = None,
    advertiser: str | None = None,
    start: int = 0,
    count: int = PAGE_SIZE,
) -> dict:
    # Verified finder params only: q/start/count/keyword/advertiser.
    # (No `countries` — unverified params risk a Rest.li 400 on every call.)
    params: dict = {"q": "criteria", "start": start, "count": count}
    if keyword:
        params["keyword"] = keyword
    if advertiser:
        params["advertiser"] = advertiser
    r = await client.get(f"{REST}/adLibrary", params=params, headers=_headers(token))
    if r.status_code != 200:
        return {"error": r.status_code, "body": r.text[:300]}
    return r.json()


async def search_all(
    *,
    keyword: str | None = None,
    advertiser: str | None = None,
    pages: int = MAX_PAGES,
) -> dict:
    """Paginated ad-library search → {"ads": [summarized...], "total": n}."""
    token = await _token()
    if not token:
        return {"skip": "no-linkedin-token"}
    collected: list[dict] = []
    total = None
    start = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for page in range(min(pages, MAX_PAGES)):
            data = await search_page(
                client, token, keyword=keyword, advertiser=advertiser, start=start
            )
            if "error" in data:
                return {"error": data["error"], "body": data.get("body", ""), "ads": collected}
            total = (data.get("paging") or {}).get("total", total)
            elements = data.get("elements") or []
            if not elements:
                break
            for el in elements:
                row = _summarize(el, keyword or advertiser or "")
                if row:
                    collected.append(row)
            start += len(elements)
            if page + 1 < pages:
                time.sleep(PAGE_DELAY_S)
    return {"ads": collected, "total": total}


async def _seed_watches(db: AsyncSession) -> int:
    existing = (await db.execute(select(LinkedInIntelWatch))).scalars().all()
    if existing:
        return 0
    for kind, value, country in DEFAULT_WATCHES:
        db.add(LinkedInIntelWatch(kind=kind, value=value, country=country))
    await db.flush()
    return len(DEFAULT_WATCHES)


async def run_watch(db: AsyncSession | None = None) -> dict:
    """Execute all enabled watch entries; upsert results; detect endings.

    Returns a diff summary for the caller (Celery task → Telegram digest).
    """
    if db is None:
        async with task_session() as session:
            return await run_watch(session)
    assert db is not None  # noqa: S101 — narrowed by the early return

    seeded = await _seed_watches(db)
    watches = (
        (await db.execute(select(LinkedInIntelWatch).where(LinkedInIntelWatch.enabled.is_(True))))
        .scalars()
        .all()
    )
    token = await _token()
    if not token:
        return {"skip": "no-linkedin-token"}

    now = datetime.now(UTC)
    seen_urls: set[str] = set()
    new_rows = 0
    updated = 0
    errors: list[str] = []

    for w in watches:
        try:
            result = await search_all(
                keyword=w.value if w.kind == "keyword" else None,
                advertiser=w.value if w.kind == "advertiser" else None,
            )
        except Exception as exc:  # one bad watch must not kill the run
            errors.append(f"{w.kind}:{w.value}: {type(exc).__name__}: {exc}")
            continue
        w.last_run_at = now
        if "error" in result:
            errors.append(f"{w.kind}:{w.value}: HTTP {result['error']}")
            continue
        for row in result.get("ads", []):
            seen_urls.add(row["ad_url"])
            existing = (
                await db.execute(select(LinkedInAdIntel).where(LinkedInAdIntel.ad_url == row["ad_url"]))
            ).scalar_one_or_none()
            if existing is None:
                db.add(LinkedInAdIntel(**row, status="active", first_seen_at=now, last_seen_at=now))
                new_rows += 1
            else:
                for k, v in row.items():
                    setattr(existing, k, v)
                existing.status = "active"
                existing.miss_count = 0
                existing.last_seen_at = now
                updated += 1
        await db.commit()

    # Ended detection: rows not seen this run accrue misses; >= threshold → ended.
    active = (await db.execute(select(LinkedInAdIntel).where(LinkedInAdIntel.status == "active"))).scalars().all()
    ended = 0
    for row in active:
        if row.ad_url in seen_urls:
            continue
        row.miss_count += 1
        if row.miss_count >= MISS_THRESHOLD:
            row.status = "ended"
            ended += 1
    await db.commit()

    summary = {
        "watches": len(watches),
        "seeded": seeded,
        "new": new_rows,
        "updated": updated,
        "ended": ended,
        "errors": errors,
    }
    logger.info("[li-adlib-watch] %s", summary)
    return summary


async def search_ads(
    *,
    advertiser: str | None = None,
    keyword: str | None = None,
    limit: int = 25,
) -> dict:
    """One-shot public search (API surface / ad-hoc use)."""
    result = await search_all(
        keyword=keyword, advertiser=advertiser, pages=max(1, -(-limit // PAGE_SIZE))
    )
    if "skip" in result or "error" in result:
        return result
    return {"ads": result["ads"][:limit], "total": result.get("total")}
