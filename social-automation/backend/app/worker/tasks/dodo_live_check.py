"""Watch for Dodo live-payments activation after merchant review.

Runs every 30 minutes via celery-beat on the default queue while
``BILLING_PROVIDER=dodo`` and ``DODO_ENVIRONMENT=live_mode``. Probes the
``MERCHANT_NOT_LIVE`` checkout gate with a deliberately invalid product id
(no real checkout is created) and, the moment it lifts, fires a Slack alert
and sets the ``billing:dodo_live_confirmed`` Redis flag so the task becomes
a permanent no-op.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

_CONFIRMED_KEY = "billing:dodo_live_confirmed"


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _redis():
    import redis.asyncio as aioredis

    from app.core.config import get_settings
    return aioredis.from_url(get_settings().REDIS_URL)


async def _check() -> dict:
    from app.core.config import get_settings
    from app.services import dodo_api
    from app.services.slack_notifications import post_alert_to_slack

    s = get_settings()
    if s.billing_provider != "dodo" or s.DODO_ENVIRONMENT.strip().lower() != "live_mode":
        return {"skipped": True, "reason": "dodo live mode not active"}

    r = await _redis()
    try:
        if await r.get(_CONFIRMED_KEY):
            return {"skipped": True, "reason": "already confirmed live"}

        live = await dodo_api.live_payments_enabled()
        if not live:
            logger.info("Dodo live payments not enabled yet — merchant review pending")
            return {"live": False}

        await r.set(_CONFIRMED_KEY, str(int(time.time())))
    finally:
        await r.aclose()

    msg = (
        ":tada: *Dodo live payments are ENABLED* — merchant review approved. "
        "Checkout endpoints now accept real payments for SocialAuto "
        "Pro / Business / Enterprise."
    )
    try:
        await post_alert_to_slack(msg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dodo live alert to Slack failed: %s", exc)
    logger.warning("Dodo live payments ENABLED — review approved")
    return {"live": True}


@celery_app.task(name="app.worker.tasks.dodo_live_check.check_dodo_live")
def check_dodo_live() -> dict:
    """Probe Dodo's live-payments gate; alert Slack once approved."""
    return _run_async(_check())
