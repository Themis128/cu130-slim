"""LinkedIn newsletter subscribers → EspoCRM sync.

LinkedIn exposes no public API for newsletter subscriber lists — the UI
loads them via an authenticated Voyager endpoint. We reuse the logged-in
linkedin-sidecar session's cookies (export_cookies) to call the exact
endpoint the dashboard calls:

    GET /voyager/api/voyagerPublishingDashSeriesSubscribers
        ?q=contentSeries&seriesUrn=urn:li:fsd_contentSeries:<id>
        &count=50&start=<n>

Subscribers carry name/headline/profile URL — no email, so Espo dedupe is
by a ``LI-URN:<urn>`` marker in the Lead description (contains-search),
and locally via a Redis set so we never re-post.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_VOYAGER = "https://www.linkedin.com/voyager/api/voyagerPublishingDashSeriesSubscribers"
_PAGE = 50
_REDIS_KEY = "linkedin-newsletter:synced-urns"


def parse_subscriber(el: dict[str, Any]) -> dict[str, str] | None:
    """One voyager element → {name, headline, urn, profile_url}."""
    urn = str(el.get("entityUrn") or el.get("urn") or "")
    if "miniProfile" not in urn and "fsd_miniProfile" not in urn:
        # some responses nest under 'subscriber'
        el = el.get("subscriber") or {}
        urn = str(el.get("entityUrn") or "")
    name = str(el.get("fullName") or el.get("name") or
               f"{el.get('firstName','')} {el.get('lastName','')}".strip())
    if not urn or not name:
        return None
    public_id = el.get("publicIdentifier") or ""
    return {
        "name": name,
        "headline": str(el.get("headline") or ""),
        "urn": urn,
        "profile_url": f"https://www.linkedin.com/in/{public_id}/" if public_id else "",
    }


async def fetch_subscribers(cookies: dict[str, str], series_urn: str, *,
                            max_pages: int = 40) -> list[dict[str, str]]:
    """Paginate the voyager subscriber feed with the sidecar's cookies."""
    csrf = (cookies.get("JSESSIONID") or "").strip('"')
    li_at = cookies.get("li_at")
    if not csrf or not li_at:
        raise RuntimeError("linkedin cookies missing li_at/JSESSIONID — session dead")
    headers = {
        "csrf-token": csrf,
        "cookie": f'JSESSIONID="{csrf}"; li_at={li_at}',
        "user-agent": "Mozilla/5.0",
        "x-restli-protocol-version": "2.0.0",
        "accept": "application/vnd.linkedin.normalized+json+2.1",
    }
    out: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=30) as c:
        start = 0
        while start < max_pages * _PAGE:
            r = await c.get(_VOYAGER, params={
                "q": "contentSeries", "seriesUrn": series_urn,
                "count": _PAGE, "start": start}, headers=headers)
            r.raise_for_status()
            data = r.json()
            elements = data.get("elements") or []
            for el in elements:
                sub = parse_subscriber(el)
                if sub:
                    out.append(sub)
            if len(elements) < _PAGE:
                break
            start += _PAGE
    return out


async def espo_upsert_subscriber(sub: dict[str, str]) -> bool:
    """Create an EspoCRM Lead for the subscriber if the LI-URN is new.
    Dedupes on the marker in description (Espo 'contains' where)."""
    from app.services.espocrm_client import espo_get, espo_post
    urn = sub["urn"]
    found = await espo_get("/Lead", params={"searchParams": json.dumps({
        "maxSize": 1, "select": ["id"],
        "where": [{"type": "contains", "attribute": "description", "value": urn}],
    })})
    if found.get("list"):
        return False
    first, _, last = sub["name"].partition(" ")
    await espo_post("/Lead", {
        "firstName": first, "lastName": last or "(LinkedIn)",
        "source": "LinkedIn newsletter",
        "description": (f"LinkedIn newsletter subscriber\n{sub['headline']}\n"
                        f"{sub['profile_url']}\nLI-URN:{urn}"),
        "title": sub["headline"][:200],
    })
    return True


async def sync_newsletter_subscribers(series_urn: str, redis=None) -> dict[str, int]:
    """Full sync: sidecar cookies → voyager → dedupe → EspoCRM leads."""
    import redis.asyncio as aioredis

    from app.core.config import get_settings
    from app.services.linkedin_sidecar import LinkedInSidecarClient

    stats = {"fetched": 0, "created": 0, "skipped": 0}
    if not series_urn:
        return {"skipped": "no LINKEDIN_NEWSLETTER_SERIES_URN configured"}

    client = LinkedInSidecarClient()
    cookies = await client.export_cookies()
    subs = await fetch_subscribers(cookies, series_urn)
    stats["fetched"] = len(subs)

    r = redis or aioredis.from_url(get_settings().REDIS_URL)
    seen = await r.smembers(_REDIS_KEY)
    seen = {x.decode() if isinstance(x, bytes) else x for x in seen}
    new_urns = []
    for sub in subs:
        if sub["urn"] in seen:
            stats["skipped"] += 1
            continue
        try:
            if await espo_upsert_subscriber(sub):
                stats["created"] += 1
            else:
                stats["skipped"] += 1
            new_urns.append(sub["urn"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("subscriber upsert failed for %s: %s", sub["urn"][:40], exc)
            stats["skipped"] += 1
    if new_urns:
        await r.sadd(_REDIS_KEY, *new_urns)
    return stats
