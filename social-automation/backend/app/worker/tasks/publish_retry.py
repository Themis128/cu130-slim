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
from sqlalchemy import func, select

from app.models.content import PostTarget
from app.models.queue import PublishQueue
from app.services.error_taxonomy import TERMINAL, classify_publish_error
from app.worker._async import run_async, task_session

logger = logging.getLogger(__name__)

_MAX_RETRY_AGE_H = 72
_BACKOFF_MIN = 15


async def _sweep() -> dict:
    now = datetime.now(UTC)
    retried = skipped = 0
    async with task_session() as db:
        targets = (
            (await db.execute(select(PostTarget).where(PostTarget.status == "failed").where(PostTarget.updated_at > now - timedelta(hours=_MAX_RETRY_AGE_H))))
            .scalars()
            .all()
        )
        for tgt in targets:
            err = tgt.error_message or ""
            # Stored class wins; legacy rows classify once and persist.
            eclass = tgt.error_class or classify_publish_error(err)
            if not tgt.error_class:
                tgt.error_class = eclass
            if eclass == TERMINAL:
                skipped += 1
                continue
            if tgt.updated_at and tgt.updated_at > now - timedelta(minutes=_BACKOFF_MIN):
                skipped += 1
                continue
            # Reactivate the actual queue row — the processor picks up
            # PublishQueue, not PostTarget status. Skip when an active row
            # already exists for this (post, account): ux_publish_queue_active_target
            # allows only one, and a second pending row would violate it.
            active = (
                await db.execute(
                    select(func.count())
                    .select_from(PublishQueue)
                    .where(
                        PublishQueue.post_id == tgt.post_id,
                        PublishQueue.social_account_id == tgt.social_account_id,
                        PublishQueue.status.in_(["pending", "processing"]),
                    )
                )
            ).scalar_one()
            if active:
                skipped += 1
                continue
            qr = (
                (
                    await db.execute(
                        select(PublishQueue).where(
                            PublishQueue.post_id == tgt.post_id, PublishQueue.social_account_id == tgt.social_account_id, PublishQueue.status == "failed"
                        )
                    )
                )
                .scalars()
                .first()
            )
            if not qr:
                skipped += 1
                continue
            qr.status = "pending"
            qr.attempts = 0
            qr.locked_at = None
            tgt.status = "pending"
            tgt.error_message = None
            retried += 1
        await db.commit()
    return {"retried": retried, "skipped": skipped}


@shared_task
def run_publish_retry_sweep():
    return run_async(_sweep())
