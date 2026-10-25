"""Celery tasks — scheduled notebook-grade analytics digests.

Mirrors notebooks/weekly-analytics.ipynb: publish-queue, agency-proposal and
audit KPIs for the trailing 7 days, posted to the ops channel. The notebook
remains the interactive version; this task is the governed worker leg.
"""

import datetime as dt
import logging

from celery import shared_task
from sqlalchemy import func, select

from app.models.agency import AgencyAudit, AgencyProposal
from app.models.queue import PublishQueue
from app.worker._async import run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app
celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


async def _weekly_analytics() -> dict:
    from app.db.session import async_session_maker
    from app.services.slack_notifications import post_alert_to_slack

    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=7)
    async with async_session_maker() as db:
        pub = dict((await db.execute(
            select(PublishQueue.status, func.count()).where(PublishQueue.created_at >= since).group_by(Post.status))).all())
        prop = dict((await db.execute(
            select(AgencyProposal.status, func.count()).where(AgencyProposal.created_at >= since)
            .group_by(AgencyProposal.status))).all())
        audit = (await db.execute(
            select(func.avg(AgencyAudit.score), func.count()).where(AgencyAudit.created_at >= since))).first()

    msg = (f"*Weekly SocialAuto — {dt.date.today()}*\n"
           f"Publish last 7d: `{pub}`\n"
           f"Agency proposals: `{prop}`\n"
           f"Audits: `{audit[1]}` · avg score `{float(audit[0] or 0):.0f}`")
    await post_alert_to_slack(msg)
    return {"publish": pub, "proposals": prop, "audits": {"count": audit[1], "avg": float(audit[0] or 0)}}


@shared_task
def run_weekly_analytics() -> dict | None:
    """Weekly Monday digest — publish/agency KPIs → ops channel."""
    from app.core.config import get_settings

    if not get_settings().AGENCY_ENABLED:
        return None
    try:
        return run_async(_weekly_analytics())
    except Exception as exc:  # noqa: BLE001
        logger.warning("weekly analytics digest failed: %s", exc)
        return None
