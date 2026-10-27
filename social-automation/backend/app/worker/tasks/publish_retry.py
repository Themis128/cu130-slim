"""Retry sweep for failed publish targets.

Failed targets don't die — transient causes (media URL expired before
Meta's fetch, token contention, platform hiccups) resolve on retry.
The sweep resets failed post_targets → pending so the 30s
process-publish-queue beat picks them up:

- non-retryable errors (format rejection, missing account, token
  decrypt) stay failed — those need a human fix, not a retry loop
- backoff: a target stays failed until it has sat for
  min(5min * attempts_seen, 1h) — attempts tracked in meta-free form
  via consecutive sweeps on updated_at
- 72h age cap — past that, permanent-fail stays failed
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select

from app.models.content import PostTarget
from app.worker._async import run_async, task_session

logger = logging.getLogger(__name__)

_MAX_RETRY_AGE_H = 72
_BACKOFF_MIN = 15
_NON_RETRYABLE = (
    "does not accept",
    "Unsupported platform",
    "account not connected",
    "Token decrypt failed",
)


async def _sweep() -> dict:
    now = datetime.now(UTC)
    retried = skipped = 0
    async with task_session() as db:
        targets = (
            await db.execute(
                select(PostTarget)
                .where(PostTarget.status == "failed")
                .where(PostTarget.updated_at > now - timedelta(hours=_MAX_RETRY_AGE_H))
            )
        ).scalars().all()
        for tgt in targets:
            err = tgt.error_message or ""
            if any(tok in err for tok in _NON_RETRYABLE):
                skipped += 1
                continue
            if tgt.updated_at and tgt.updated_at > now - timedelta(minutes=_BACKOFF_MIN):
                skipped += 1
                continue
            tgt.status = "pending"
            tgt.error_message = None
            # Reactivate the actual queue row — the processor picks up
            # PublishQueue, not PostTarget status.
            from app.models.publish_queue import PublishQueue
            qr = (await db.execute(
                __import__("sqlalchemy").select(PublishQueue).where(
                    PublishQueue.post_id == tgt.post_id,
                    PublishQueue.social_account_id == tgt.social_account_id,
                    PublishQueue.status == "failed")
            )).scalars().first()
            if qr:
                qr.status = "pending"
                qr.attempts = 0
                qr.locked_at = None
            retried += 1
        await db.commit()
    return {"retried": retried, "skipped": skipped}


@shared_task
def run_publish_retry_sweep():
    return run_async(_sweep())
