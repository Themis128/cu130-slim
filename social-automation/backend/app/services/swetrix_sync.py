"""Swetrix CE sync — pull cookieless site-traffic aggregates into Postgres.

Swetrix (self-hosted at ``SWETRIX_API_URL``, default analytics.cloudless.gr)
is the full-coverage top-of-funnel truth for owned web properties: its
tracker is cookieless, so it sees every visitor — unlike the consent-gated
``web_analytics_events`` webhook stream which only fires after the user
accepts analytics cookies.

For every ``domain -> pid`` pair in ``SWETRIX_PROJECTS`` this pulls:

- a daily timeseries (visitors / pageviews / session_duration / bounce_rate)
  over ``SWETRIX_LOOKBACK_DAYS`` and upserts ``site_traffic_daily`` rows,
- a 7d breakdown snapshot (top pages / countries / referrers / utm sources)
  stored in ``meta`` on the most recent day row.

``datalake_export`` then carries the rollup to ``lake/site-traffic/daily.json``.
Every project failure degrades to an error entry — one dead project never
aborts the sweep.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.site_traffic import SiteTrafficDaily

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 20.0
_BREAKDOWN_DAYS = 7
_BREAKDOWN_LIMIT = 10
_DAILY_METRICS = "visitors,pageviews,session_duration,bounce_rate"
_BREAKDOWN_DIMS = ("page", "country", "referrer", "utm_source")


def project_map() -> dict[str, str]:
    """Parse ``SWETRIX_PROJECTS`` JSON into ``{domain: pid}``."""
    raw = (settings.SWETRIX_PROJECTS or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("SWETRIX_PROJECTS is not valid JSON — skipping sync")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if k and v}


def _base() -> str:
    return settings.SWETRIX_API_URL.rstrip("/") + "/backend/v2"


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = await client.get(
        f"{_base()}/projects/{path}",
        params=params,
        headers={"x-api-key": settings.SWETRIX_API_KEY},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


async def _timeseries(client: httpx.AsyncClient, pid: str) -> list[dict[str, Any]]:
    today = datetime.now(UTC).date()
    frm = (today - timedelta(days=settings.SWETRIX_LOOKBACK_DAYS)).isoformat()
    payload = await _get(
        client,
        f"{pid}/traffic/timeseries",
        {
            "from": frm,
            "to": today.isoformat(),
            "timeBucket": "day",
            "metrics": _DAILY_METRICS,
            "timezone": settings.SWETRIX_TIMEZONE,
        },
    )
    return payload.get("data") or []


async def _breakdown(client: httpx.AsyncClient, pid: str, dimension: str) -> list[dict[str, Any]]:
    try:
        payload = await _get(
            client,
            f"{pid}/traffic/breakdown",
            {
                "period": f"{_BREAKDOWN_DAYS}d",
                "dimension": dimension,
                "metrics": "visitors,pageviews",
                "limit": _BREAKDOWN_LIMIT,
                "timezone": settings.SWETRIX_TIMEZONE,
            },
        )
    except httpx.HTTPStatusError as exc:
        # Unknown/unsupported dimension for this project — skip, don't fail.
        logger.info("swetrix breakdown %s/%s skipped: %s", pid, dimension, exc.response.status_code)
        return []
    return payload.get("data") or []


def _day_of(ts: str) -> date | None:
    try:
        return datetime.fromisoformat(ts).date()
    except (TypeError, ValueError):
        return None


async def _upsert_day(
    db: AsyncSession,
    team_id,
    domain: str,
    pid: str,
    day: date,
    row: dict[str, Any],
) -> SiteTrafficDaily:
    existing = (
        await db.execute(
            select(SiteTrafficDaily).where(
                SiteTrafficDaily.team_id == team_id,
                SiteTrafficDaily.domain == domain,
                SiteTrafficDaily.date == day,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = SiteTrafficDaily(team_id=team_id, domain=domain, swetrix_pid=pid, date=day)
        db.add(existing)
    existing.swetrix_pid = pid
    existing.visitors = int(row.get("visitors") or 0)
    existing.pageviews = int(row.get("pageviews") or 0)
    existing.bounce_rate = float(row.get("bounce_rate") or 0.0)
    existing.session_duration_sec = float(row.get("session_duration") or 0.0)
    existing.synced_at = datetime.now(UTC)
    return existing


async def sync_project(db: AsyncSession, domain: str, pid: str, team_id) -> dict[str, Any]:
    """Sync one Swetrix project; returns a per-project result dict."""
    result: dict[str, Any] = {"domain": domain, "pid": pid, "days": 0, "error": None}
    async with httpx.AsyncClient() as client:
        series = await _timeseries(client, pid)
        breakdowns = {dim: await _breakdown(client, pid, dim) for dim in _BREAKDOWN_DIMS}

    latest: SiteTrafficDaily | None = None
    for entry in series:
        day = _day_of(entry.get("timestamp", ""))
        if day is None:
            continue
        row = await _upsert_day(db, team_id, domain, pid, day, entry)
        result["days"] += 1
        if latest is None or day > latest.date:
            latest = row

    if latest is not None:
        latest.meta = {
            "top_pages": breakdowns.get("page", []),
            "top_countries": breakdowns.get("country", []),
            "top_referrers": breakdowns.get("referrer", []),
            "utm_sources": breakdowns.get("utm_source", []),
        }
    await db.commit()
    return result


async def sync_all_swetrix(db: AsyncSession) -> dict[str, Any]:
    """Sweep every configured Swetrix project into ``site_traffic_daily``."""
    if not settings.SWETRIX_API_KEY:
        return {"ok": False, "error": "SWETRIX_API_KEY not configured", "projects": []}
    projects = project_map()
    if not projects:
        return {"ok": False, "error": "SWETRIX_PROJECTS not configured", "projects": []}

    try:
        team_id = uuid.UUID(settings.SWETRIX_TEAM_ID)
    except (ValueError, AttributeError):
        return {"ok": False, "error": f"SWETRIX_TEAM_ID invalid: {settings.SWETRIX_TEAM_ID}", "projects": []}

    results: list[dict[str, Any]] = []
    for domain, pid in sorted(projects.items()):
        try:
            results.append(await sync_project(db, domain, pid, team_id))
        except Exception as exc:  # noqa: BLE001 — one project never aborts the sweep
            logger.warning("swetrix sync failed for %s (%s): %s", domain, pid, exc)
            results.append({"domain": domain, "pid": pid, "days": 0, "error": str(exc)[:200]})
    return {
        "ok": all(not r.get("error") for r in results),
        "projects": results,
        "days_synced": sum(r.get("days", 0) for r in results),
    }
