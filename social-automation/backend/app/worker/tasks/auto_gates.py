"""Opt-in human-gate automations (user directive 2026-10-27: 'fully automate them').

- ``survey_autosend`` — sends ``kind='survey'`` campaigns whose
  ``meta_data['auto_send']`` is true once they have pending recipients.
- ``post_autoapprove`` — promotes REVIEW → APPROVED when env
  ``AUTO_APPROVE_POSTS=true``; writes the same PostComment + audit log the
  manual approve endpoint writes (author marked 'autopilot').
Both are OFF unless opted in — the env flag / campaign meta is the recorded
human approval (Code of Honor #2: approval is given once, as configuration).
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import select

from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()
logger = logging.getLogger(__name__)


async def _survey_autosend() -> dict:
    from app.models.email_campaign import EmailCampaign, EmailRecipient, RecipientStatus
    from app.services.email_campaigns import send_campaign

    sent = 0
    async with task_session() as db:
        camps = (await db.execute(select(EmailCampaign).where(EmailCampaign.kind == "survey", EmailCampaign.status.in_(["draft", "sent"])))).scalars().all()
        for c in camps:
            if not (c.meta_data or {}).get("auto_send"):
                continue
            pending = (
                await db.execute(select(EmailRecipient.id).where(EmailRecipient.campaign_id == c.id, EmailRecipient.status == RecipientStatus.pending))
            ).first()
            if pending:
                await send_campaign(c.id)
                sent += 1
    return {"sent": sent}


async def _post_autoapprove() -> dict:
    if os.environ.get("AUTO_APPROVE_POSTS", "").lower() not in ("1", "true", "yes"):
        return {"approved": 0, "off": True}
    from app.models.content import Post, PostStatus

    approved = 0
    async with task_session() as db:
        posts = (await db.execute(select(Post).where(Post.status == PostStatus.REVIEW))).scalars().all()
        for post in posts:
            post.status = PostStatus.APPROVED
            approved += 1
            try:
                from app.models.content import PostComment

                db.add(PostComment(post_id=post.id, user_id=post.user_id, author_name="autopilot", body="Auto-approved (autopilot)", action="approve"))
            except Exception:
                logger.debug("comment skipped", exc_info=True)
        await db.commit()
    return {"approved": approved}


@celery_app.task(name="app.worker.tasks.auto_gates.survey_autosend")
def survey_autosend() -> dict:
    return run_async(_survey_autosend())


@celery_app.task(name="app.worker.tasks.auto_gates.post_autoapprove")
def post_autoapprove() -> dict:
    return run_async(_post_autoapprove())


# ── Engagement-gate autonomy dial ──
# AUTONOMY_LEVEL env (Code of Honor #2: approval given once, as config):
#   0 = manual (default) — every gated action waits for a human click
#   1 = notify-only — same as 0, used for future escalation hooks
#   2 = auto-safe — approve only low-risk actions (comment/duet_flag with a
#       drafted comment and score >= threshold); reposts still gate
#   3 = full — every pending_human_gate re-enters dispatch; the human gate
#       becomes an audit log, not a blocker
def _autonomy_level() -> int:
    try:
        return int(os.environ.get("AUTONOMY_LEVEL", "0"))
    except ValueError:
        return 0


_SAFE_ACTIONS = {"comment", "duet_flag"}
_SCORE_MIN = 60


async def _engagement_autoapprove() -> dict:
    lvl = _autonomy_level()
    if lvl < 2:
        return {"approved": 0, "off": True, "level": lvl}
    from app.models.engagement import EngagementAction

    approved = 0
    async with task_session() as db:
        rows = (await db.execute(
            select(EngagementAction).where(
                EngagementAction.status == "pending_human_gate")
            .order_by(EngagementAction.score.desc()).limit(50))).scalars().all()
        for r in rows:
            if lvl == 2 and not (
                    r.action in _SAFE_ACTIONS
                    and r.comment_draft
                    and (r.score or 0) >= _SCORE_MIN):
                continue
            r.status = "pending"  # same transition the manual /approve does
            approved += 1
            try:
                from app.services.slack_notifications import notify_engagement_autoapprove
                notify_engagement_autoapprove(r)
            except Exception:
                logger.debug("autoapprove notify skipped", exc_info=True)
        await db.commit()
    return {"approved": approved, "level": lvl}


@celery_app.task(name="app.worker.tasks.auto_gates.engagement_autoapprove")
def engagement_autoapprove() -> dict:
    return run_async(_engagement_autoapprove())
