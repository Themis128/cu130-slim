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
    from app.services.dmr import call_dmr_chat
    from app.services.engagement import (
        EngagementCandidate as Cand,
    )
    from app.services.engagement import (
        build_3c_prompt,
        queue_engagement,
    )

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
            from app.services.engagement import classify_vector
            vector, override = classify_vector(
                cand.author_name + " " + cand.author_handle, cand.post_text)
            resp = await call_dmr_chat(
                build_3c_prompt(cand.post_text, cand.author_name or cand.author_handle,
                                voice_hints=["professional", "technical", "direct"],
                                vector_override=override),
                platform="linkedin", temperature=0.6, max_tokens=120)
            a.comment_draft = (resp.get("text") or "").strip().strip('"') or None
        await db.commit()

        # real-time per-item gate pings — dedicated channel webhook with
        # native deep links (platform:// schemes open the mobile app)
        if drafts_needed:
            try:
                import os

                from app.core.config import get_settings
                from app.services.slack_notifications import _post_slack_text

                cfg = get_settings()
                hook = (os.environ.get("SLACK_ENGAGEMENT_WEBHOOK_URL")
                        or cfg.SLACK_ALERTS_WEBHOOK_URL)
                chan = (os.environ.get("SLACK_ENGAGEMENT_CHANNEL_ID")
                        or cfg.SLACK_ALERTS_CHANNEL_ID)
                deeplink = {
                    "linkedin": "linkedin://feed",
                    "instagram": "instagram://app",
                    "facebook": "fb://feed",
                    "tiktok": "snssdk1233://",
                    "twitter": "twitter://timeline",
                    "threads": "instagram://app",
                }
                for a in drafts_needed:
                    if not a.comment_draft:
                        continue
                    dl = deeplink.get(a.platform, "")
                    draft = (a.comment_draft[:140] + "…"
                             if len(a.comment_draft) > 140
                             else a.comment_draft)
                    await _post_slack_text(
                        webhook_url=hook,
                        token=os.environ.get("SLACK_BOT_TOKEN", ""),
                        channel_id=chan,
                        purpose="engagement gate",
                        text=(f":hourglass_flowing_sand: *{a.platform.title()} "
                              f"{a.action} draft → @{a.target_author}* "
                              f"(score {a.score:.1f})\n"
                              f"> _{draft}_\n"
                              + (f"<{dl}|📱 Open source post> · " if dl else "")
                              + "<https://cloudless.gr/admin/socialauto|"
                                "⚡ Open Control Gate>"))
                    try:
                        from app.services.telegram_notify import send_telegram
                        await send_telegram(
                            f"⏳ {a.platform} {a.action} → @{a.target_author} "
                            f"(score {a.score:.1f})\n\n{draft}\n\n"
                            f"cloudless.gr/admin/socialauto")
                    except Exception:
                        pass
            except Exception as exc:  # noqa: BLE001 — alerting never breaks the task
                logger.warning("gate alert failed: %s", exc)

        # 3. dispatch auto-approved actions (likes) in score order
        from datetime import UTC, datetime

        from app.services.engagement_actors import (
            fb_actor,
            ig_actor,
            linkedin_actor,
            threads_actor,
            tiktok_actor,
            x_actor,
        )
        actors = {"twitter": x_actor.act,
                  "threads": threads_actor.act,
                  "linkedin": linkedin_actor.act,
                  "instagram": ig_actor.act,
                  "facebook": fb_actor.act,
                  "tiktok": tiktok_actor.act}
        rows = (await db.execute(select(EngagementAction).where(
            EngagementAction.status.in_(["pending", "pending_actor"])).order_by(
            EngagementAction.score.desc()).limit(limit))).scalars().all()
        done, failed = [], []
        for r in rows:
            actor = actors.get(r.platform)
            if not actor:
                r.status = "pending_actor"
                failed.append(f"{r.platform}:no-actor")
                continue
            res = await actor(r, db)
            if res.get("ok"):
                r.status = "acted"
                r.acted_at = datetime.now(UTC)
                done.append(f"{r.platform}:{r.action}@{r.target_author}")
            else:
                r.status = "pending_actor" if res.get("skip") else "failed"
                failed.append(f"{r.target_author}:{res.get('error','?')[:60]}")
        await db.commit()
        return {"scored": len(fresh), "drafted": len(drafts_needed),
                "dispatched": done, "failed": failed}
