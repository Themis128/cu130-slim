"""Nextcloud → media library auto-sync.

Walks a configured folder (default ``Photos/``), imports every new
image/video as a MediaAsset, and skips files already imported —
tracked via ``meta_data.nextcloud_path``.

Beat runs it hourly; tune via NEXTCLOUD_SYNC_FOLDER /
NEXTCLOUD_SYNC_ENABLED env.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from celery import shared_task
from sqlalchemy import select

logger = logging.getLogger(__name__)


@shared_task(name="app.worker.tasks.nextcloud_sync.sync_nextcloud_media",
             bind=True, max_retries=1)
def sync_nextcloud_media(self, folder: str | None = None) -> dict[str, Any]:
    from app.worker._async import run_async

    return run_async(_sync(folder))


async def _sync(folder: str | None) -> dict[str, Any]:
    from app.core.config import get_settings
    from app.db.session import async_session_maker
    from app.models.content import MediaAsset
    from app.models.user import TeamMember, User
    from app.services.media_sources import MediaSourceError, nextcloud_download, nextcloud_list
    from app.services.media_storage import save_uploaded_media

    s = get_settings()
    if not getattr(s, "NEXTCLOUD_SYNC_ENABLED", True):
        return {"skipped": "disabled"}
    folder = folder or getattr(s, "NEXTCLOUD_SYNC_FOLDER", "") or "Photos"

    try:
        files = [f for f in await nextcloud_list(folder)
                 if not f.is_dir and f.mime_type.startswith(("image/", "video/"))]
    except MediaSourceError as e:
        logger.warning("[nc-sync] list failed: %s", e)
        return {"error": str(e)[:200]}

    async with async_session_maker() as db:
        u = (await db.execute(select(User))).scalars().first()
        tm = (await db.execute(
            select(TeamMember).where(TeamMember.user_id == u.id))).scalars().first()

        # already-imported paths
        rows = (await db.execute(
            select(MediaAsset.meta_data["nextcloud_path"].astext)
            .where(MediaAsset.meta_data["nextcloud_path"].astext.isnot(None)))
        ).scalars().all()
        seen = set(rows)

        imported, errors = [], []
        for f in files:
            if f.id in seen:
                continue
            try:
                data, mime = await nextcloud_download(f.id)
                asset = await save_uploaded_media(
                    db, team_id=tm.team_id, user_id=u.id, content=data,
                    original_filename=f.name, mime_type=mime,
                    alt_text=f"Nextcloud sync — {f.name}",
                    tags="nextcloud,auto-sync", width=0, height=0)
                asset.meta_data = {**(asset.meta_data or {}),
                                   "nextcloud_path": f.id}
                imported.append(f.name)
            except Exception as e:  # per-file, keep going
                errors.append({"path": f.id, "error": str(e)[:150]})
        await db.commit()

    logger.info("[nc-sync] %s: %d new, %d skipped, %d errors",
                folder, len(imported), len(files) - len(imported) - len(errors),
                len(errors))
    return {"folder": folder, "new": imported, "skipped": len(seen),
            "errors": errors}
