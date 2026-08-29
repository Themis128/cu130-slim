"""Celery task: daily SocialAuto digest → Slack #socialauto + email."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.services.slack_digest import run_daily_digest_for_all_teams
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


@shared_task(name="app.worker.tasks.digest.send_daily_slack_digest")
def send_daily_slack_digest(
    days: int = 1,
    post_to_slack: bool = True,
    post_to_email: bool = True,
) -> dict[str, Any]:
    """Build and deliver the SocialAuto daily analytics + issues digest."""
    return asyncio.run(
        _send_digest_async(
            days=days, post_to_slack=post_to_slack, post_to_email=post_to_email
        )
    )


async def _send_digest_async(
    *, days: int, post_to_slack: bool, post_to_email: bool
) -> dict[str, Any]:
    async with _worker_db() as db:
        reports = await run_daily_digest_for_all_teams(
            db,
            days=days,
            post_to_slack=post_to_slack,
            post_to_email=post_to_email,
        )
    return {
        "teams": len(reports),
        "posted_slack": sum(1 for r in reports if r.get("posted_to_slack")),
        "emailed": sum(1 for r in reports if r.get("emailed")),
        "errors": [
            r.get("slack_error") or r.get("email_error")
            for r in reports
            if r.get("slack_error") or r.get("email_error")
        ],
        "issue_counts": [
            {
                "team": r.get("team_name"),
                "issues": len(r.get("issues") or []),
            }
            for r in reports
        ],
    }
