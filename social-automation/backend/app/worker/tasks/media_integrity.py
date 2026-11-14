"""Celery task — recurring media storage integrity sweep.

The task is deliberately bounded per run and writes an AuditLog artifact for
every team, including quiet runs. Missing objects are quarantined in place;
backend-label drift is repaired only when the same storage path exists on a
verified alternate backend.
"""

import logging

from celery import shared_task
from sqlalchemy import select

from app.models.user import Team
from app.services import media_integrity
from app.services.db_sync import sync_after_worker_task
from app.worker._async import run_async, task_session

logger = logging.getLogger(__name__)


@shared_task
def run_media_integrity_sweep(batch_size: int = 1000, notify: bool = True) -> dict:
    """Check media DB rows against local/MinIO/R2 object reality."""
    try:
        return run_async(_run(batch_size=batch_size, notify=notify))
    except Exception as exc:  # noqa: BLE001
        logger.warning("media integrity sweep failed: %s", exc, exc_info=True)
        return {"error": str(exc)}


async def _run(*, batch_size: int, notify: bool) -> dict:
    async with task_session() as db:
        teams = (await db.execute(select(Team))).scalars().all()
        reports = []
        for team in teams:
            try:
                reports.append(
                    await media_integrity.run_integrity_sweep(
                        db,
                        team.id,
                        batch_size=batch_size,
                        notify=notify,
                    )
                )
                await db.commit()
            except Exception as exc:  # noqa: BLE001 — continue with other teams
                await db.rollback()
                logger.warning("media integrity sweep failed for team %s: %s", team.id, exc, exc_info=True)
                reports.append({"team_id": str(team.id), "error": str(exc)})

    try:
        await sync_after_worker_task(["media_assets", "audit_logs"])
    except Exception:  # noqa: BLE001 — sync is best-effort after durable writes
        logger.debug("media integrity D1 sync failed", exc_info=True)

    return {
        "teams": len(teams),
        "checked": sum(r.get("checked", 0) for r in reports),
        "repaired": sum(r.get("repaired", 0) for r in reports),
        "missing": sum(r.get("missing", 0) for r in reports),
        "size_mismatch": sum(r.get("size_mismatch", 0) for r in reports),
        "backend_errors": sum(r.get("backend_errors", 0) for r in reports),
        "reports": reports,
    }
