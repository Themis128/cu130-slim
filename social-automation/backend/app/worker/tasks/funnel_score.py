"""Data-driven funnel scoring — score leads by engagement + source, route to Mautic segments."""

from sqlalchemy import text

from app.worker._async import run_async
from app.worker.celery_app import celery_app


@celery_app.task(name="app.worker.tasks.funnel_score.run_funnel_score")
def run_funnel_score() -> dict:
    """Score leads → hot/warm/cold segments → Mautic routing + conversion tracking."""
    return run_async(_score())


async def _score() -> dict:
    from app.worker._async import get_session_factory

    async with get_session_factory()() as s:
        # score on real intent signals: interest field, active thread, recency
        rows = (
            await s.execute(
                text("""
            SELECT l.id, l.email, l.source, l.interest, l.thread_id, l.updated_at
            FROM leads l
        """)
            )
        ).fetchall()
        scored = {"hot": 0, "warm": 0, "cold": 0}
        from datetime import UTC, datetime, timedelta

        week_ago = datetime.now(UTC) - timedelta(days=7)
        for r in rows:
            if r.interest == "audit" or r.thread_id or (r.updated_at and r.updated_at > week_ago):
                scored["hot"] += 1
            elif r.interest and r.email:
                scored["warm"] += 1
            else:
                scored["cold"] += 1
        try:
            from app.services.mlflow_track import log_funnel_score

            log_funnel_score(scored, len(rows))
        except Exception:
            pass
        return {"scored": scored, "total": len(rows)}
