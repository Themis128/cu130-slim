"""Datalake-insights sweep — compute decision signals from the lake source
tables and persist them for the next export + notify the alert channel."""

from __future__ import annotations

from celery import shared_task
from sqlalchemy import select

from app.models.user import Team
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()


@shared_task(name="app.worker.tasks.datalake_insights.datalake_insights_sweep")
def datalake_insights_sweep() -> dict:
    """Compute datalake signals per team, persist the event, ping Slack."""
    from app.services import datalake_insights as di
    from app.services.slack_notifications import post_alert_to_slack

    async def _run() -> dict:
        async with task_session() as db:
            teams = (await db.execute(select(Team.id))).scalars().all()
            out = []
            # E2E/test teams massively outnumber real ones and render
            # identical all-zero signal lines — dedupe so the alert channel
            # gets one copy per distinct digest, not one per team.
            seen: set[str] = set()
            for tid in teams:
                signals = await di.apply_signals(db, tid)
                lines = di.render_slack_lines(signals)
                if len(lines) > 1:  # only ping when there is something to say
                    text = "\n".join(lines)
                    if text not in seen:
                        seen.add(text)
                        await post_alert_to_slack(text)
                out.append({"team": str(tid), "actions": len(signals["actions"])})
            return {"teams": len(out), "alerts": len(seen), "details": out}

    return run_async(_run())
