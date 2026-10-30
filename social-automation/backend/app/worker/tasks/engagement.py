"""Engagement sweep — score new candidates, draft 3C comments, dispatch
queued actions honoring jitter + caps."""

import logging
from datetime import UTC, datetime

from celery import shared_task
from sqlalchemy import select

logger = logging.getLogger(__name__)


@shared_task(name="app.worker.tasks.engagement.dispatch_engagements", bind=True, max_retries=1)
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
        fresh = (await db.execute(select(EngagementCandidate).where(EngagementCandidate.scored == False).limit(50))).scalars().all()  # noqa: E712
        for c in fresh:
            age = (c.ingested_at - c.posted_at).total_seconds()
            await queue_engagement(
                Cand(
                    platform=c.platform,
                    author_handle=c.author_handle,
                    author_name=c.author_name,
                    post_id=c.post_id,
                    post_text=c.post_text,
                    posted_seconds_ago=max(age, 0),
                    connection_tier=c.connection_tier,
                    is_followed=c.is_followed,
                    is_connection=c.is_connection,
                    in_crm=c.in_crm,
                    meta_data=c.meta_data,
                ),
                db,
            )
            c.scored = True
        await db.commit()

        # auto-approve repost actions scoring >= the gate threshold — high-
        # confidence reposts skip the human review queue (post_autoapprove pattern)
        hi = (await db.execute(
            select(EngagementAction).where(
                EngagementAction.action == "repost",
                EngagementAction.status == "pending_human_gate",
                EngagementAction.score >= 8.0))).scalars().all()
        for a in hi:
            a.status = "approved"
        if hi:
            await db.commit()

        # 2. draft 3C comments for gated comment actions
        drafts_needed = (
            (
                await db.execute(
                    select(EngagementAction)
                    .where(EngagementAction.action == "comment", EngagementAction.comment_draft.is_(None), EngagementAction.status == "pending_human_gate")
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for a in drafts_needed:
            cand = (await db.execute(select(EngagementCandidate).where(EngagementCandidate.post_id == a.target_post_id))).scalars().first()
            if not cand:
                continue
            from app.services.engagement import classify_vector

            vector, override = classify_vector(cand.author_name + " " + cand.author_handle, cand.post_text)
            resp = await call_dmr_chat(
                build_3c_prompt(
                    cand.post_text, cand.author_name or cand.author_handle, voice_hints=["professional", "technical", "direct"], vector_override=override
                ),
                platform="linkedin",
                temperature=0.6,
                max_tokens=120,
            )
            a.comment_draft = (resp.get("text") or "").strip().strip('"') or None
        await db.commit()

        # real-time per-item gate pings — dedicated channel webhook with
        # native deep links (platform:// schemes open the mobile app)
        # Ping only the rows drafted in this sweep, so each gated comment is
        # announced once. Re-querying every pending_human_gate row from the
        # last 2h re-sent <!channel> for the same items on every beat tick.
        if drafts_needed:
            try:
                import os

                from app.core.config import get_settings
                from app.services.slack_notifications import _post_slack_text

                cfg = get_settings()
                hook = os.environ.get("SLACK_ENGAGEMENT_WEBHOOK_URL") or cfg.SLACK_ALERTS_WEBHOOK_URL
                chan = os.environ.get("SLACK_ENGAGEMENT_CHANNEL_ID") or cfg.SLACK_ALERTS_CHANNEL_ID
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
                        continue  # nothing to review (no candidate / empty LLM reply)
                    dl = deeplink.get(a.platform, "")
                    draft = a.comment_draft[:140] + "…" if len(a.comment_draft) > 140 else a.comment_draft
                    await _post_slack_text(
                        webhook_url=hook,
                        token=os.environ.get("SLACK_BOT_TOKEN", ""),
                        channel_id=chan,
                        purpose="engagement gate",
                        text=(
                            f"<!channel> :hourglass_flowing_sand: *{a.platform.title()} "
                            f"{a.action} draft → @{a.target_author}* "
                            f"(score {a.score:.1f})\n"
                            f"> _{draft}_\n" + (f"<{dl}|📱 Open source post> · " if dl else "") + "<https://cloudless.gr/admin/socialauto|"
                            "⚡ Open Control Gate>"
                        ),
                    )
                    try:
                        from app.services.telegram_notify import send_telegram

                        await send_telegram(
                            f"⏳ {a.platform} {a.action} → @{a.target_author} (score {a.score:.1f})\n\n{draft}\n\ncloudless.gr/admin/socialauto"
                        )
                    except Exception:
                        pass
                    a.meta_data = {**(a.meta_data or {}), "gate_notified": "1"}
            except Exception as exc:  # noqa: BLE001 — alerting never breaks the task
                logger.warning("gate alert failed: %s", exc)

        # 3. dispatch auto-approved actions (likes) in score order
        from app.services.engagement_actors import (
            fb_actor,
            ig_actor,
            linkedin_actor,
            skool_actor,
            threads_actor,
            tiktok_actor,
            x_actor,
        )

        actors = {
            "twitter": x_actor.act,
            "threads": threads_actor.act,
            "linkedin": linkedin_actor.act,
            "instagram": ig_actor.act,
            "facebook": fb_actor.act,
            "tiktok": tiktok_actor.act,
            "skool": skool_actor.act,
        }
        rows = (
            (
                await db.execute(
                    select(EngagementAction)
                    .where(EngagementAction.status.in_(["pending", "pending_actor"]), EngagementAction.action != "duet_flag")
                    .order_by(EngagementAction.score.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
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
                failed.append(f"{r.target_author}:{res.get('error', '?')[:60]}")
        await db.commit()
        return {"scored": len(fresh), "drafted": len(drafts_needed), "dispatched": done, "failed": failed}


ENGAGE_KEYWORDS = [
    "SMB IT support", "small business IT", "digital agency automation",
    "website maintenance small business", "affordable web design",
    "IT services SMB", "cloud hosting small business",
]


@shared_task(name="app.worker.tasks.engagement.engagement_ingest", bind=True, max_retries=1)
def engagement_ingest(self, limit: int = 20):
    """Feed the funnel — search X for keyword matches → EngagementCandidate rows."""
    from datetime import datetime

    from app.models.engagement import EngagementCandidate
    from app.services.brand_monitoring import search_twitter_mentions

    async def _run():
        env = dict(os.environ)
        if os.path.exists("/app/.env"):
            env.update(dict(x.split("=", 1) for x in open("/app/.env").read().splitlines() if "=" in x and not x.startswith("#")))
        bearer = env.get("TWITTER_BEARER_TOKEN", "") or env.get("X_BEARER_TOKEN", "")
        if not bearer:
            return {"ingested": 0, "reason": "no bearer"}
        seen = set()
        n = 0
        async with task_session() as db:
            have = (await db.execute(select(EngagementCandidate.post_id))).scalars().all()
            seen = set(have)
            for kw in ENGAGE_KEYWORDS:
                for m in await search_twitter_mentions(kw, bearer, max_results=10):
                    pid = (m.get("url") or "").rsplit("/", 1)[-1]
                    if not pid or pid in seen:
                        continue
                    db.add(EngagementCandidate(
                        platform="twitter",
                        author_handle=m.get("author", ""),
                        post_id=pid,
                        post_text=m.get("content", "")[:2000],
                        posted_at=datetime.fromisoformat(m["mentioned_at"].replace("Z", "+00:00")) if m.get("mentioned_at") else datetime.utcnow(),
                        meta_data={"source": "keyword", "keyword": kw, "engagement": m.get("engagement", 0)},
                    ))
                    seen.add(pid)
                    n += 1
                    if n >= limit:
                        break
                if n >= limit:
                    break
            await db.commit()
        return {"ingested": n}
    return _run_sync(_run())
