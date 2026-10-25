"""Engagement sweep — score new candidates, draft 3C comments, dispatch
queued actions honoring jitter + caps."""
import logging
from celery import shared_task
from sqlalchemy import select

logger = logging.getLogger(__name__)


@shared_task(name="app.worker.tasks.engagement.dispatch_engagements",
             bind=True, max_retries=1)
def dispatch_engagements(self, limit: int = 10):
    from app.worker._async import run_async
    return run_async(_run(limit))


async def _run(limit: int):
    from app.db.session import async_session_maker
    from app.models.engagement import EngagementAction, EngagementCandidate
    from app.services.engagement import (
        EngagementCandidate as Cand,
        build_3c_prompt,
        queue_engagement,
    )
    from app.services.dmr import call_dmr_chat

    async with async_session_maker() as db:
        # 1. score unscored candidates → queue actions
        fresh = (await db.execute(select(EngagementCandidate).where(
            EngagementCandidate.scored == False).limit(50))).scalars().all()  # noqa: E712
        for c in fresh:
            age = (c.ingested_at - c.posted_at).total_seconds()
            await queue_engagement(Cand(
                platform=c.platform, author_handle=c.author_handle,
                author_name=c.author_name, post_id=c.post_id,
                post_text=c.post_text, posted_seconds_ago=max(age, 0),
                connection_tier=c.connection_tier, is_followed=c.is_followed,
                is_connection=c.is_connection, in_crm=c.in_crm), db)
            c.scored = True
        await db.commit()

        # 2. draft 3C comments for gated comment actions
        drafts_needed = (await db.execute(select(EngagementAction).where(
            EngagementAction.action == "comment",
            EngagementAction.comment_draft.is_(None),
            EngagementAction.status == "pending_human_gate").limit(limit))
        ).scalars().all()
        for a in drafts_needed:
            cand = (await db.execute(select(EngagementCandidate).where(
                EngagementCandidate.post_id == a.target_post_id))).scalars().first()
            if not cand:
                continue
            resp = await call_dmr_chat(
                build_3c_prompt(cand.post_text, cand.author_name or cand.author_handle,
                                voice_hints=["professional", "technical", "direct"]),
                platform="linkedin", temperature=0.6, max_tokens=120)
            a.comment_draft = (resp.get("text") or "").strip().strip('"') or None
        await db.commit()

        # 3. dispatch auto-approved actions (likes) in score order
        rows = (await db.execute(select(EngagementAction).where(
            EngagementAction.status == "pending").order_by(
            EngagementAction.score.desc()).limit(limit))).scalars().all()
        done = []
        for r in rows:
            r.status = "dispatched"
            done.append(f"{r.platform}:{r.action}@{r.target_author}")
        await db.commit()
        return {"scored": len(fresh), "drafted": len(drafts_needed),
                "dispatched": done}
