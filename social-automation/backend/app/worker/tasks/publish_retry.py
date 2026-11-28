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
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.models.content import PostTarget
from app.models.queue import PublishQueue, QueueStatus
from app.services.error_taxonomy import TERMINAL, classify_publish_error, is_auth_terminal
from app.worker._async import run_async, task_session

logger = logging.getLogger(__name__)

_MAX_RETRY_AGE_H = 72
_BACKOFF_MIN = 15


async def _sweep() -> dict:
    now = datetime.now(UTC)
    retried = skipped = 0
    async with task_session() as db:
        targets = (
            (
                await db.execute(
                    select(PostTarget)
                    .where(PostTarget.status == "failed")
                    .where(PostTarget.updated_at > now - timedelta(hours=_MAX_RETRY_AGE_H))
                    .options(selectinload(PostTarget.post), selectinload(PostTarget.social_account))
                )
            )
            .scalars()
            .all()
        )
        for tgt in targets:
            err = tgt.error_message or ""
            # Stored class wins; legacy rows classify once and persist.
            eclass = tgt.error_class or classify_publish_error(err)
            if not tgt.error_class:
                tgt.error_class = eclass
            auth_revive = False
            if eclass == TERMINAL:
                # Auth-terminal failures parked forever used to need a human
                # requeue after credentials healed. Now: if the account is
                # active again and the error was credential-shaped, revive
                # ONCE per (post, account) — marker lives in post.meta_data.
                post = getattr(tgt, "post", None)
                meta = dict(post.meta_data or {}) if post else {}
                done = list(meta.get("auth_resurrected") or [])
                key = f"{tgt.post_id}:{tgt.social_account_id}"
                account = getattr(tgt, "social_account", None)
                if account and getattr(account, "status", None) == "active" and is_auth_terminal(err) and key not in done:
                    auth_revive = True
                else:
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
            if auth_revive:
                post = tgt.post
                if post is not None:
                    meta = dict(post.meta_data or {})
                    done = list(meta.get("auth_resurrected") or [])
                    done.append(f"{tgt.post_id}:{tgt.social_account_id}")
                    meta["auth_resurrected"] = done
                    post.meta_data = meta
                    flag_modified(post, "meta_data")
            qr.status = QueueStatus.PENDING
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
