"""Celery task — daily LinkedIn Page invite-to-follow batch.

Company Pages get monthly invite credits that expire unused at each
refill. Sends a daily batch (default 40) via the browser bridge so the
pool is consumed before Sep 30 refill, then keeps running monthly —
when credits hit 0 the service short-circuits until the next refill.

Runs at 10:30 Europe/Athens (beat entry in celery_app.py) — business
hours deliver better acceptance than overnight sends.

After each send the result is recorded as a ``linkedin_page_invite``
initiative event so the Growth-initiatives card stays current without
manual logging (the "Log LinkedIn invites" button remains for manual
off-platform batches).
"""

import asyncio
import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, select

from app.models.analytics import AnalyticsEvent
from app.models.social_account import SocialAccount
from app.models.user import Team
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


_worker_db = task_session


def _run_async(coro):
    """Run an async coroutine in a sync Celery context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            result: dict[str, Any] = {}

            def _runner():
                new_loop = asyncio.new_event_loop()
                try:
                    result["value"] = new_loop.run_until_complete(coro)
                except Exception as exc:
                    result["error"] = exc
                finally:
                    new_loop.close()

            t = threading.Thread(target=_runner)
            t.start()
            t.join()
            if "error" in result:
                raise result["error"]
            return result.get("value")
    except RuntimeError:
        pass
    return run_async(coro)


async def _log_invite_event(res: dict[str, Any]) -> None:
    """Persist a linkedin_page_invite initiative event from a batch result.

    Only writes on a confirmed send (units > 0) or a confirmed credit
    exhaustion read — refreshes ``credits_left`` without inflating sent
    counts. Skips silently when the batch produced no signal.
    """
    from app.services.growth_initiatives import record_initiative_event

    sent = int(res.get("sent") or 0)
    no_credits = res.get("reason") == "no_credits"
    if res.get("status") != "sent" and not no_credits:
        return
    units = sent if res.get("status") == "sent" else 0
    credits_left = 0 if no_credits else max(0, int(res.get("credits_available") or 0) - sent)

    async with _worker_db() as db:
        teams = (await db.execute(select(Team))).scalars().all()
        for team in teams:
            acct = (
                await db.execute(
                    select(SocialAccount.id)
                    .where(
                        SocialAccount.team_id == team.id,
                        SocialAccount.platform == "linkedin",
                        SocialAccount.status == "active",
                    )
                    .order_by(
                        case(
                            (SocialAccount.account_type == "organization", 0),
                            else_=1,
                        )
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if not acct:
                continue
            # Idempotency: the same batch must not double-record if the task
            # is retried or re-run manually within the same window.
            dup = (
                await db.execute(
                    select(AnalyticsEvent.id).where(
                        AnalyticsEvent.team_id == team.id,
                        AnalyticsEvent.event_type == "linkedin_page_invite",
                        AnalyticsEvent.occurred_at >= datetime.now(UTC) - timedelta(hours=12),
                        AnalyticsEvent.meta_data["units"].astext == str(units),
                        AnalyticsEvent.meta_data["note"].astext == "auto daily batch",
                    )
                )
            ).first()
            if dup:
                continue
            await record_initiative_event(
                db,
                team.id,
                "linkedin_page_invite",
                platform="linkedin",
                account_id=acct,
                units=units,
                note="auto daily batch",
                credits_left=credits_left,
            )


@celery_app.task(name="app.worker.tasks.linkedin_invites.send_linkedin_invites")
def send_linkedin_invites(batch_size: int = 40) -> dict:
    """Send a daily batch of LinkedIn Page follow invitations."""
    from app.services.linkedin_invites import send_invite_batch

    res = _run_async(send_invite_batch(batch_size))
    logger.info("linkedin_invites: %s", res)
    try:
        _run_async(_log_invite_event(res))
    except Exception:  # noqa: BLE001 — event logging must never break the send task
        logger.exception("linkedin_invites: failed to record initiative event")
    return res
