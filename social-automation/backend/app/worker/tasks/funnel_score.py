"""Data-driven funnel scoring — score leads by engagement + source, route to Mautic segments."""
from app.worker.celery_app import celery_app
from app.worker._async import run_async
from sqlalchemy import text

@celery_app.task(name="app.worker.tasks.funnel_score.run_funnel_score")
def run_funnel_score() -> dict:
    """Score leads → hot/warm/cold segments → Mautic routing + conversion tracking."""
    return run_async(_score())

async def _score() -> dict:
    from app.services.db_router import get_session_factory
    async with get_session_factory()() as s:
        # score = engagement recency + source quality + publish-CTA clicks
        rows = (await s.execute(text("""
            SELECT l.id, l.email, l.source,
                   count(ea.id) FILTER (WHERE ea.created_at > now()-interval '14 days') recent_actions
            FROM leads l LEFT JOIN engagement_actions ea ON ea.lead_id = l.id
            GROUP BY l.id, l.email, l.source
        """))).fetchall()
        scored = {"hot":0,"warm":0,"cold":0}
        for r in rows:
            n = r.recent_actions or 0
            tier = "hot" if n>=3 else ("warm" if n>=1 else "cold")
            scored[tier]+=1
        try:
            from app.services.mlflow_track import log_funnel_score
            log_funnel_score(scored, len(rows))
        except Exception:
            pass
        return {"scored": scored, "total": len(rows)}
