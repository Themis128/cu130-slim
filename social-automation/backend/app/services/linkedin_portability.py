"""LinkedIn DMA data-portability products — Member DP + Pages DP.

Two regulatory API surfaces, both self-gating:

- **Member DP (Member)** — ``r_dma_portability_self_serve``. Snapshot export
  of the member's own LinkedIn data (``/rest/memberSnapshotData``) plus the
  28-day changelog (``/rest/memberChangeLogs``). Pinned to
  ``Linkedin-Version: 202312`` — these endpoints reject any other version.
- **Pages DP** — ``r_dma_admin_pages_content`` (+ ``r_dma_admin_pages_list``).
  Organization page export: post URN listing, post hydration, engagement,
  content analytics, follows. Pinned to ``Linkedin-Version: 202604``.
  Feed/follows finders are rate-limited to ~1 req/60s and data lags ~48h.

Every helper returns ``{"ok": False, "status": <http>, "not_granted": True}``
on 401/403 so callers can no-op until the portal product grants land — no
exceptions, no alert spam. Raw PII stays inside the caller's own infra.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import httpx

logger = logging.getLogger(__name__)

REST = "https://api.linkedin.com/rest"
MEMBER_DP_VERSION = "202312"
PAGES_DP_VERSION = "202604"
MEMBER_DP_HDRS = {
    "Linkedin-Version": MEMBER_DP_VERSION,
    "X-Restli-Protocol-Version": "2.0.0",
}
PAGES_DP_HDRS = {
    "Linkedin-Version": PAGES_DP_VERSION,
    "X-Restli-Protocol-Version": "2.0.0",
}

# Professional-profile domains for memberSnapshotData (domain is case-sensitive).
MEMBER_DOMAINS: tuple[str, ...] = (
    "PROFILE",
    "POSITIONS",
    "EDUCATION",
    "SKILLS",
    "CERTIFICATIONS",
    "PROJECTS",
    "ORGANIZATIONS",
    "LANGUAGES",
    "HONORS",
    "COURSES",
    "PUBLICATIONS",
    "PATENTS",
    "VOLUNTEERING_EXPERIENCES",
    "RECOMMENDATIONS",
    "CONNECTIONS",
)

MAX_PAGES_PER_DOMAIN = 10  # 50 elements/page → ≤500 elements/domain per run
FEED_PAGE_MAX = 100  # maxPaginationCount for dmaFeedContentsExternal


def _denied(status: int, body: str = "") -> dict[str, Any]:
    return {"ok": False, "status": status, "not_granted": status in (401, 403), "body": body[:300]}


# ── Member DP ────────────────────────────────────────────────────────────


async def member_snapshot(
    client: httpx.AsyncClient,
    token: str,
    *,
    domain: str | None = None,
    start: int = 0,
    count: int = 50,
) -> dict[str, Any]:
    """One page of ``memberSnapshotData?q=criteria``. Omit ``domain`` for all."""
    params: dict[str, Any] = {"q": "criteria", "start": start, "count": count}
    if domain:
        params["domain"] = domain
    try:
        r = await client.get(
            f"{REST}/memberSnapshotData",
            params=params,
            headers={"Authorization": f"Bearer {token}", **MEMBER_DP_HDRS},
        )
    except httpx.HTTPError as exc:
        return {"ok": False, "status": 0, "error": type(exc).__name__}
    if r.status_code != 200:
        return _denied(r.status_code, r.text)
    return {"ok": True, "data": r.json()}


async def member_snapshot_domain(
    client: httpx.AsyncClient,
    token: str,
    domain: str,
    *,
    max_pages: int = MAX_PAGES_PER_DOMAIN,
) -> dict[str, Any]:
    """Paginate one snapshot domain via ``paging.links`` rel=next hrefs."""
    elements: list[dict[str, Any]] = []
    total: int | None = None
    pages = 0
    url: str | None = f"{REST}/memberSnapshotData"
    params: dict[str, Any] = {"q": "criteria", "domain": domain, "start": 0, "count": 50}
    while pages < max_pages:
        try:
            if url != f"{REST}/memberSnapshotData":
                r = await client.get(url, headers={"Authorization": f"Bearer {token}", **MEMBER_DP_HDRS})
            else:
                r = await client.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {token}", **MEMBER_DP_HDRS},
                )
        except httpx.HTTPError as exc:
            return {"ok": False, "status": 0, "error": type(exc).__name__}
        if r.status_code != 200:
            return _denied(r.status_code, r.text)
        data = r.json() or {}
        pages += 1
        elements.extend(data.get("elements") or [])
        paging = data.get("paging") or {}
        if total is None:
            total = paging.get("total")
        next_href = next(
            (lnk.get("href") for lnk in paging.get("links") or [] if lnk.get("rel") == "next"),
            None,
        )
        if not next_href:
            break
        url = urllib.parse.urljoin("https://api.linkedin.com", next_href)
    return {"ok": True, "domain": domain, "elements": elements, "total": total, "pages": pages}


async def member_changelog(
    client: httpx.AsyncClient,
    token: str,
    *,
    start_time: str | None = None,
    count: int = 50,
) -> dict[str, Any]:
    """``memberChangeLogs?q=memberAndApplication`` — events from the last 28d."""
    params: dict[str, Any] = {"q": "memberAndApplication", "count": count}
    if start_time:
        params["startTime"] = start_time
    try:
        r = await client.get(
            f"{REST}/memberChangeLogs",
            params=params,
            headers={"Authorization": f"Bearer {token}", **MEMBER_DP_HDRS},
        )
    except httpx.HTTPError as exc:
        return {"ok": False, "status": 0, "error": type(exc).__name__}
    if r.status_code != 200:
        return _denied(r.status_code, r.text)
    return {"ok": True, "data": r.json()}


# ── Pages DP ─────────────────────────────────────────────────────────────


async def _pages_get(
    client: httpx.AsyncClient,
    token: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    raw_query: str | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", **PAGES_DP_HDRS}
    try:
        if raw_query is not None:
            r = await client.get(f"{REST}{path}?{raw_query}", headers=headers)
        else:
            r = await client.get(f"{REST}{path}", params=params, headers=headers)
    except httpx.HTTPError as exc:
        return {"ok": False, "status": 0, "error": type(exc).__name__}
    if r.status_code != 200:
        return _denied(r.status_code, r.text)
    return {"ok": True, "data": r.json()}


async def pages_organizations(client: httpx.AsyncClient, token: str, *, parent_org_id: str) -> dict[str, Any]:
    """``dmaOrganizations?q=parentOrganization`` — pages under an org entity."""
    return await _pages_get(
        client,
        token,
        "/dmaOrganizations",
        params={"q": "parentOrganization", "parent": parent_org_id},
    )


async def pages_feed_page(
    client: httpx.AsyncClient,
    token: str,
    org_urn: str,
    *,
    cursor: str | None = None,
) -> dict[str, Any]:
    """One cursor page of ``dmaFeedContentsExternal?q=postsByAuthor``.

    Cursor pagination: ``metadata.nextPaginationCursor`` → ``paginationCursor``.
    Feed finders are limited to ~1 req/60s — callers must space invocations.
    """
    params: dict[str, Any] = {
        "q": "postsByAuthor",
        "author": org_urn,
        "maxPaginationCount": FEED_PAGE_MAX,
    }
    if cursor:
        params["paginationCursor"] = cursor
    return await _pages_get(client, token, "/dmaFeedContentsExternal", params=params)


async def pages_batch_get(
    client: httpx.AsyncClient,
    token: str,
    path: str,
    ids: list[str],
    *,
    chunk: int = 50,
) -> list[dict[str, Any]]:
    """rest.li 2.0 BATCH_GET — ``ids=List(<url-encoded urns>)``."""
    out: list[dict[str, Any]] = []
    for i in range(0, len(ids), chunk):
        batch = ids[i : i + chunk]
        ids_param = "List(" + ",".join(urllib.parse.quote(u, safe="") for u in batch) + ")"
        res = await _pages_get(client, token, path, raw_query=f"ids={ids_param}")
        if not res.get("ok"):
            return out
        data = res.get("data") or {}
        # BATCH_GET results arrive as {"results": {...}} or a bare map
        results = data.get("results") if isinstance(data, dict) else None
        if isinstance(results, dict):
            out.extend(v for v in results.values() if isinstance(v, dict))
        elif isinstance(data, dict):
            out.extend(v for v in data.values() if isinstance(v, dict))
    return out


async def pages_content_trend(
    client: httpx.AsyncClient,
    token: str,
    *,
    source_entity: str,
    metric_types: str,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    """``dmaOrganizationalPageContentAnalytics?q=trend`` — one ≤12-month window.

    ``timeIntervals`` uses rest.li positional syntax — build the raw query.
    """
    time_intervals = f"(timeRange:(start:{start_ms},end:{end_ms}),timeGranularityType:DAY)"
    raw = (
        "q=trend"
        f"&sourceEntity={urllib.parse.quote(source_entity, safe='')}"
        f"&metricTypes={metric_types}"
        f"&timeIntervals={urllib.parse.quote(time_intervals, safe='()/:,')}"
    )
    return await _pages_get(client, token, "/dmaOrganizationalPageContentAnalytics", raw_query=raw)
