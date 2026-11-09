"""Post-publish verification for Reels/Stories.

The "watch the first real publish" gap: a reel/story publish can return a
plausible id and still die on the platform side (missing reels scope,
story upload rejected after the fact). This task reconciles every recent
publish whose platform_specific.publish_path is an ephemeral surface and
records platform_specific.publish_verified = "live" | "failed".

Beat: every 2h (registered in celery_app.py). Stories only verify <24h
old — they expire by design.
"""

import json
import logging
import os
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text as sa_text

from app.services.instagrapi_client import InstagrapiClient, InstagrapiError
from app.services.slack_notifications import send_telegram_alert
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

VERIFY_PATHS = ("graph_reels", "instagrapi_story")
WINDOW_HOURS = 48


async def _verify_reel(post_id: str, session) -> str:
    """Check a Page Reel exists via Graph /{video_id}."""
    row = (
        await session.execute(
            sa_text(
                """
                select s.access_token
                from post_targets t
                join social_accounts s on s.id = t.account_id
                where t.post_id = cast(:pid as uuid) and t.platform = 'facebook'
                limit 1
                """
            ),
            {"pid": post_id},
        )
    ).first()
    if not row or not row[0]:
        return "unknown"
    video_id = row[0]
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"https://graph.facebook.com/v21.0/{video_id}",
            params={"fields": "id", "access_token": os.environ.get("FB_PAGE_TOKEN", "")},
        )
    return "live" if r.status_code == 200 else "failed"


async def _verify_story(post: dict, session) -> str:
    """Check an IG Story exists via instagrapi story list (<=24h only)."""
    published_at = post.get("published_at")
    if published_at and datetime.now(UTC) - published_at > timedelta(hours=23):
        return "unknown"  # expired by design; can't prove or disprove
    user = os.environ.get("INSTAGRAM_USERNAME", "").strip()
    pwd = os.environ.get("INSTAGRAM_PASSWORD", "").strip()
    if not user or not pwd:
        return "unknown"
    try:
        client = InstagrapiClient(username=user, password=pwd, proxy=(os.environ.get("INSTAGRAM_PROXY") or None))
        stories = await client.get_own_stories()
        wanted = str(post.get("platform_post_id") or "")
        return "live" if any(str(s.get("pk") or s.get("id")) == wanted or wanted in str(s.get("id", "")) for s in stories) else "failed"
    except InstagrapiError:
        return "unknown"


async def _run() -> dict:
    summary = {"checked": 0, "live": 0, "failed": 0, "unknown": 0}
    async with task_session() as session:
        rows = (
            await session.execute(
                sa_text(
                    """
                    select p.id::text as pid, p.platform_specific as ps,
                           p.published_at,
                           t.platform_post_id as platform_post_id
                    from posts p
                    join post_targets t on t.post_id = p.id
                    where p.platform_specific->>'publish_path' = any(:paths)
                      and coalesce(p.platform_specific->>'publish_verified','') = ''
                      and p.published_at > now() - interval :win
                    limit 25
                    """
                ),
                {"paths": list(VERIFY_PATHS), "win": f"{WINDOW_HOURS} hours"},
            )
        ).all()

        for pid, ps, published_at, platform_post_id in rows:
            summary["checked"] += 1
            post = {"published_at": published_at, "platform_post_id": platform_post_id}
            path = (ps or {}).get("publish_path", "")
            if path == "graph_reels":
                verdict = await _verify_reel(platform_post_id, session)
            else:
                verdict = await _verify_story(post, session)
            summary[verdict] += 1

            new_ps = {**(ps or {}), "publish_verified": verdict}
            await session.execute(
                sa_text("update posts set platform_specific = cast(:ps as jsonb) where id = cast(:pid as uuid)"),
                {"ps": json.dumps(new_ps), "pid": pid},
            )
            if verdict == "failed":
                await send_telegram_alert(
                    "P2",
                    f"Publish verification FAILED: {path} post {pid[:8]} "
                    f"not found live (platform id {platform_post_id})",
                )
        await session.commit()
    return summary


@celery_app.task(name="app.worker.tasks.publish_verify.verify_publish_paths", bind=True, max_retries=1)
def verify_publish_paths(self) -> dict:  # noqa: ARG001
    return run_async(_run())
