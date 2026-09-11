"""Celery analytics sync — pull LinkedIn (etc.) post metrics into Postgres."""
import asyncio
from contextlib import asynccontextmanager

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.user import Team
from app.services.analytics_sync import sync_team_analytics
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()


@asynccontextmanager
async def _worker_db():
    engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@shared_task
def sync_all_analytics() -> dict:
    """Fetch platform analytics for every team and store snapshots in Postgres.

    Analytics tables (analytics_events, post_analytics_snapshots) are
    Postgres-primary — they are NOT synced to D1 to stay within the free
    tier write limit (100K rows/day).
    """
    return asyncio.run(_sync_all_analytics_async())


async def _sync_all_analytics_async() -> dict:
    from typing import Any

    summary: dict[str, Any] = {"teams": 0, "synced": 0, "errors": []}
    async with _worker_db() as db:
        teams = (await db.execute(select(Team))).scalars().all()
        for team in teams:
            summary["teams"] += 1
            try:
                result = await sync_team_analytics(db, team.id, days=365)
                summary["synced"] += result.synced
                summary["errors"].extend(result.errors[:20])
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                summary["errors"].append(f"team {team.id}: {exc}")
    return summary


@shared_task
def sync_team_analytics_task(team_id: str, days: int = 365) -> dict:
    """Fetch analytics for a single team. Postgres-primary (no D1 sync)."""
    return asyncio.run(_sync_team_async(team_id, days))


async def _sync_team_async(team_id: str, days: int) -> dict:
    import uuid

    async with _worker_db() as db:
        result = await sync_team_analytics(db, uuid.UUID(team_id), days=days)
        return {
            "synced": result.synced,
            "skipped": result.skipped,
            "errors": result.errors,
            "snapshots": result.snapshots[:50],
        }
