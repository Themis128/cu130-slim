"""Celery task — daily LinkedIn Page invite-to-follow batch.

Company Pages get monthly invite credits that expire unused at each
refill. Sends a daily batch (default 40) via the browser bridge so the
pool is consumed before Sep 30 refill, then keeps running monthly —
when credits hit 0 the service short-circuits until the next refill.

Runs at 10:30 Europe/Athens (beat entry in celery_app.py) — business
hours deliver better acceptance than overnight sends.
"""
import asyncio
import logging
import threading
from typing import Any

from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


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
    return asyncio.run(coro)


@celery_app.task(name="app.worker.tasks.linkedin_invites.send_linkedin_invites")
def send_linkedin_invites(batch_size: int = 40) -> dict:
    """Send a daily batch of LinkedIn Page follow invitations."""
    from app.services.linkedin_invites import send_invite_batch

    res = _run_async(send_invite_batch(batch_size))
    logger.info("linkedin_invites: %s", res)
    return res
