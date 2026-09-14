"""Celery tasks for Telegram group digests (Bot API sendMessage to owner)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import decrypt_token
from app.services.telegram_api import TelegramAPIClient
from app.services.telegram_group_watch import (
    get_group_watch_from_meta,
    send_digest_for_account,
)
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _worker_db():
    engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def _run_digests() -> dict:
    from app.models.social_account import SocialAccount

    summary: dict = {"accounts": 0, "sent": 0, "results": []}
    async with _worker_db() as db:
        rows = (
            await db.execute(
                select(SocialAccount).where(
                    SocialAccount.platform == "telegram",
                    SocialAccount.status == "active",
                )
            )
        ).scalars().all()

        for account in rows:
            cfg = get_group_watch_from_meta(account.meta_data)
            if not cfg.get("enabled") or not cfg.get("digest_enabled", True):
                continue
            if not cfg.get("owner_chat_id"):
                continue
            summary["accounts"] += 1
            try:
                raw = account.access_token_enc
                if isinstance(raw, bytes):
                    token = decrypt_token(raw)
                else:
                    enc = (account.meta_data or {}).get("bot_token_enc")
                    token = decrypt_token(enc) if enc else ""
                if not token:
                    summary["results"].append(
                        {"account_id": str(account.id), "error": "no_token"}
                    )
                    continue
                client = TelegramAPIClient(token)
                result = await send_digest_for_account(
                    client=client,
                    account_id=str(account.id),
                    cfg=cfg,
                    force=False,
                )
                summary["sent"] += int(result.get("sent") or 0)
                summary["results"].append({"account_id": str(account.id), **result})
            except Exception as exc:
                logger.warning(
                    "Telegram digest failed account=%s: %s",
                    account.id,
                    str(exc)[:200],
                )
                summary["results"].append(
                    {"account_id": str(account.id), "error": str(exc)[:200]}
                )
    return summary


@celery_app.task(name="app.worker.tasks.telegram_digest.send_telegram_group_digests")
def send_telegram_group_digests() -> dict:
    """Hourly beat: send daily digests when local hour matches digest_hour."""
    return asyncio.run(_run_digests())
