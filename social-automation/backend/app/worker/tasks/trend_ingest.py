"""Trend-signal ingest — persist scored signals from all trend feeds.

Runs the Google Trends and TikTok Creative Center fetches, scores every
signal through the shared niche matrix (scripts.trendwatch.build_matrix),
and upserts deduped TrendSignal rows keyed on
(source, query, geo, period_key). This closes the "trends are ephemeral"
gap: Metabase + the R2 datalake export get a durable, joinable history.
"""

from __future__ import annotations

import datetime
import logging

from celery import shared_task
from sqlalchemy.dialects.postgresql import insert as pg_insert

logger = logging.getLogger(__name__)

GOOGLE_GEOS = ("GR", "US", "GB")


def _google_signals(today: str) -> list[dict]:
    from scripts.trendwatch import build_matrix, fetch_trending

    rows = []
    for geo in GOOGLE_GEOS:
        try:
            rows.extend(build_matrix(fetch_trending(geo)))
        except Exception as exc:  # noqa: BLE001 — one geo down != skip all
            logger.warning("google trends %s failed: %s", geo, exc)
    return [
        {
            "source": "google-trends",
            "query": r["query"],
            "geo": r["geo"],
            "traffic": r.get("traffic") or 0,
            "classification": r["classification"],
            "swot": r["swot"],
            "period_key": today,
            "meta_data": {"news": r.get("news") or []},
        }
        for r in rows
    ]


def _tiktok_signals() -> list[dict]:
    try:
        from app.services.tiktok_trends import LIGHT_SWEEP, fetch_trending_videos  # type: ignore[import-not-found]

        from scripts.trendwatch import build_matrix
    except ImportError:  # tiktok trendwatch not yet merged — degrade, don't fail
        logger.warning("tiktok_trends module unavailable — skipping TT feed")
        return []
    try:
        videos = fetch_trending_videos(sweep=LIGHT_SWEEP)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tiktok CC fetch failed: %s", exc)
        return []
    out = []
    for r in build_matrix(videos):
        tt = r.get("tiktok") or {}
        out.append(
            {
                "source": "tiktok-cc",
                "query": r["query"],
                "geo": r["geo"],
                "traffic": r.get("traffic") or 0,
                "classification": r["classification"],
                "swot": r["swot"],
                "period_key": tt.get("week_ending") or "",
                "meta_data": {"tiktok": {k: v for k, v in tt.items() if k != "also_ranked"}},
            }
        )
    return out


async def _run() -> dict:
    from app.db.session import async_session_maker
    from app.models.trends import TrendSignal

    today = datetime.date.today().isoformat()
    signals = _google_signals(today) + _tiktok_signals()
    if not signals:
        return {"inserted": 0, "skipped": "no signals"}

    async with async_session_maker() as db:
        stmt = pg_insert(TrendSignal).values(signals)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_trend_signal")
        res = await db.execute(stmt)
        await db.commit()
    return {"fetched": len(signals), "inserted": res.rowcount}


@shared_task(name="app.worker.tasks.trend_ingest.ingest_trends", bind=True, max_retries=0)
def ingest_trends(self):
    from app.worker._async import run_async

    return run_async(_run())
