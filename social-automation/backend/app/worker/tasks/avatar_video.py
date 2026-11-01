"""Celery task: generate a captioned avatar story video and store it.

Usage:

    generate_avatar_story.delay(
        scenes=[{"clip": "sad", "line1": "A founder spent €40,000",
                 "line2": "on a website that did nothing.", "duration": 7},
                {"clip": "happy", "line1": "3x more leads",
                 "line2": "in 4 weeks.", "duration": 9}],
        filename="maria-story.mp4",
        alt_text="...",
    )

``avatar_asset_id`` optionally points to a media_assets row holding the
(stylized) avatar PNG; ``source_photo_asset_id`` to a photo to cartoon-ify
via FLUX img2img. With neither, a default mascot avatar is generated.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="app.worker.tasks.avatar_video.generate_avatar_story", bind=True, max_retries=1)
def generate_avatar_story(
    self,
    *,
    scenes: list[dict[str, Any]],
    filename: str = "avatar-story.mp4",
    alt_text: str = "AI-generated avatar story video",
    tags: str = "ai-video,avatar",
    team_id: str | None = None,
    user_id: str | None = None,
    avatar_asset_id: str | None = None,
    source_photo_asset_id: str | None = None,
    music_url: str | None = None,
    width: int = 576,
    height: int = 1024,
) -> dict:
    from app.worker._async import run_async

    return run_async(
        _run(
            scenes=scenes,
            filename=filename,
            alt_text=alt_text,
            tags=tags,
            team_id=team_id,
            user_id=user_id,
            avatar_asset_id=avatar_asset_id,
            source_photo_asset_id=source_photo_asset_id,
            music_url=music_url,
            width=width,
            height=height,
        )
    )


async def _run(**kw) -> dict:
    import httpx
    from sqlalchemy import select

    from app.models.content import MediaAsset  # noqa: F401 (registry)
    from app.models.user import TeamMember, User
    from app.services.avatar_video import Scene, generate_avatar_video
    from app.services.media_storage import save_uploaded_media
    from app.worker._async import task_session

    scenes = [Scene(clip=s["clip"], line1=s.get("line1", ""), line2=s.get("line2", ""), duration=float(s.get("duration", 6))) for s in kw["scenes"]]

    avatar_png = source_photo = None
    # app.db.session has no get_session_factory; task_session() is the
    # per-worker-loop session used by every other worker task.
    async with task_session() as db:
        uid = uuid.UUID(kw["user_id"]) if kw["user_id"] else None
        tid = uuid.UUID(kw["team_id"]) if kw["team_id"] else None
        if uid is None or tid is None:
            u = (await db.execute(select(User))).scalars().first()
            tm = (await db.execute(select(TeamMember).where(TeamMember.user_id == u.id))).scalars().first()
            uid, tid = u.id, tm.team_id

        async def _asset_bytes(asset_id: str | None):
            if not asset_id:
                return None
            q = select(MediaAsset).where(MediaAsset.id == uuid.UUID(asset_id))
            if tid is not None:
                q = q.where(MediaAsset.team_id == tid)
            row = (await db.execute(q)).scalar_one_or_none()
            if not row:
                return None
            from app.services.media_dedup import load_asset_bytes

            return await load_asset_bytes(row)

        avatar_png = await _asset_bytes(kw["avatar_asset_id"])
        source_photo = await _asset_bytes(kw["source_photo_asset_id"])

        music = None
        if kw["music_url"]:
            # SSRF guard — the route validates too, but a task can be
            # enqueued directly (beat, script, replay). Re-validate here
            # and never follow redirects: a public URL must not bounce
            # the fetch inward.
            try:
                from app.services.url_safety import validate_public_http_url

                safe_music_url = validate_public_http_url(kw["music_url"], allow_http=False)
            except Exception:
                safe_music_url = None
                logger.warning("avatar-story music_url failed SSRF validation — skipping music")
            if safe_music_url:
                async with httpx.AsyncClient(timeout=60, follow_redirects=False) as c:
                    r = await c.get(safe_music_url)
                    music = r.content if r.status_code == 200 else None

        video, meta = await generate_avatar_video(
            avatar_png=avatar_png, source_photo=source_photo, scenes=scenes, music_bytes=music, width=kw["width"], height=kw["height"]
        )

        asset = await save_uploaded_media(
            db,
            team_id=tid,
            user_id=uid,
            content=video,
            original_filename=kw["filename"],
            mime_type="video/mp4",
            alt_text=kw["alt_text"],
            tags=kw["tags"],
            width=kw["width"],
            height=kw["height"],
        )
        await db.commit()
        return {"asset_id": str(asset.id), "bytes": len(video), "meta": meta}
