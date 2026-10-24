"""Celery task — sync the Kakkava corpus folder into team doctrine collections.

Daily sweep: walks ``settings.KAKKAVA_DIR`` (read-only bind mount of the host
OneDrive "Kakkava Sofia" folder), extracts text from new/changed files, and
upserts chunks into each team's Chroma ``team_<id>_doctrine`` collection via
``app.services.kakkava.sync_corpus``. Idempotent — unchanged files are skipped
by content hash, so a quiet day costs nothing.
"""

import logging

from celery import shared_task
from sqlalchemy import select

from app.services import kakkava
from app.worker._async import get_session_factory, run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


async def _sync_all_teams() -> dict:
    from app.models.user import AuditLog, Team

    results = {}
    session_factory = get_session_factory()
    async with session_factory() as db:
        teams = (await db.execute(select(Team))).scalars().all()
        for team in teams:
            try:
                stats = await kakkava.sync_corpus(team.id)
            except Exception as exc:
                logger.warning("kakkava sync failed for team %s: %s", team.id, exc)
                stats = {"error": str(exc)}
            results[str(team.id)] = stats
            # Observable artifact — every run, even a quiet one, so the
            # workforce audit can verify this worker clocked in.
            db.add(AuditLog(
                team_id=team.id,
                action="kakkava-sync",
                resource_type="sweep",
                detail=(
                    f"files={stats.get('files', 0)} unchanged={stats.get('unchanged', 0)} "
                    f"added={stats.get('chunks_added', 0)} removed={stats.get('chunks_removed', 0)} "
                    f"errors={stats.get('errors', 0)}"
                ),
                meta=stats if isinstance(stats, dict) else {},
            ))
        await db.commit()
    return results


@shared_task
def sync_kakkava_corpus() -> dict | None:
    """Sync the Kakkava doctrine corpus for every team (idempotent)."""
    if not kakkava.kakkava_enabled():
        return None
    try:
        return run_async(_sync_all_teams())
    except Exception as exc:
        logger.warning("kakkava corpus sync failed: %s", exc)
        return {"error": str(exc)}
