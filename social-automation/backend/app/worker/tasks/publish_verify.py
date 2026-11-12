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

from app.core.security import decrypt_token
from app.services.instagrapi_client import InstagrapiClient, InstagrapiError
from app.services.slack_notifications import send_telegram_alert
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

# publish_path values written by app/services/publishing.py onto
# post.platform_specific via PublishResult.platform_meta.
VERIFY_PATHS = ("graph_reels", "sidecar_story")
WINDOW_HOURS = 48
GRAPH_VERSION = os.environ.get("FACEBOOK_GRAPH_VERSION", "v21.0")


async def _verify_reel(platform_post_id: str, access_token_enc, session) -> str:
    """Check a Page Reel exists via Graph GET /{video_id}?fields=id.

    ``platform_post_id`` is the facebook post_target's id (the reel
    video_id). ``access_token_enc`` is that target account's encrypted
    page token — tokens are per-account, never env.
    """
    if not platform_post_id or not access_token_enc:
        return "unknown"
    try:
        token = decrypt_token(access_token_enc)
    except Exception:
        return "unknown"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{platform_post_id}",
            params={"fields": "id", "access_token": token},
        )
    if r.status_code == 200:
        return "live"
    if r.status_code in (400, 404):
        return "failed"
    return "unknown"


async def _verify_story(platform_post_id: str, published_at, session) -> str:
    """Check an IG Story exists via instagrapi story list (<=24h only)."""
    if published_at and datetime.now(UTC) - published_at > timedelta(hours=23):
        return "unknown"  # expired by design; can't prove or disprove
    user = os.environ.get("INSTAGRAM_USERNAME", "").strip()
    pwd = os.environ.get("INSTAGRAM_PASSWORD", "").strip()
    if not user or not pwd or not platform_post_id:
        return "unknown"
    try:
        client = InstagrapiClient(username=user, password=pwd, proxy=(os.environ.get("INSTAGRAM_PROXY") or None))
        stories = await client.get_own_stories()
        wanted = str(platform_post_id)
        return "live" if any(str(s.get("pk") or s.get("id")) == wanted or wanted in str(s.get("id", "")) for s in stories) else "failed"
    except InstagrapiError:
        return "unknown"


async def _run() -> dict:
    summary = {"checked": 0, "live": 0, "failed": 0, "unknown": 0}
    async with task_session() as session:
        # One row per post_target (per account). platform_specific.
        # publish_path is per-post, so a multi-platform post can surface
        # twice — each row is verified against ITS platform's target id.
        rows = (
            await session.execute(
                sa_text(
                    """
                    select distinct on (p.id, s.platform)
                           p.id::text as pid, p.platform_specific as ps,
                           p.published_at,
                           s.platform as platform,
                           t.platform_post_id as platform_post_id,
                           s.access_token_enc as access_token_enc
                    from posts p
                    join post_targets t on t.post_id = p.id
                    join social_accounts s on s.id = t.social_account_id
                    where p.platform_specific->>'publish_path' = any(cast(:paths as text[]))
                      and coalesce(p.platform_specific->>'publish_verified','') = ''
                      and p.published_at > now() - cast(:win as interval)
                      and t.status = 'published'
                    order by p.id, s.platform
                    limit 25
                    """
                ),
                {"paths": list(VERIFY_PATHS), "win": timedelta(hours=WINDOW_HOURS)},
            )
        ).all()

        for pid, ps, published_at, platform, platform_post_id, token_enc in rows:
            summary["checked"] += 1
            path = (ps or {}).get("publish_path", "")
            if path == "graph_reels" and platform == "facebook":
                verdict = await _verify_reel(str(platform_post_id or ""), token_enc, session)
            elif path == "sidecar_story" and platform == "instagram":
                verdict = await _verify_story(str(platform_post_id or ""), published_at, session)
            else:
                verdict = "unknown"
            summary[verdict] += 1

            # Only stamp the verdict once — a post verified via its first
            # matching target shouldn't be re-checked by sibling targets.
            if verdict != "unknown":
                new_ps = {**(ps or {}), "publish_verified": verdict}
                await session.execute(
                    sa_text("update posts set platform_specific = cast(:ps as jsonb) where id = cast(:pid as uuid)"),
                    {"ps": json.dumps(new_ps), "pid": pid},
                )
            if verdict == "failed":
                await send_telegram_alert(
                    "P2",
                    f"Publish verification FAILED: {path} {platform} post {pid[:8]} not found live (platform id {platform_post_id})",
                )
        await session.commit()
    return summary


@celery_app.task(name="app.worker.tasks.publish_verify.verify_publish_paths", bind=True, max_retries=1)
def verify_publish_paths(self) -> dict:  # noqa: ARG001
    return run_async(_run())
