"""datalake_executor — turns datalake_insights actions into work.

Hops:
1. action → execution: content_topic → draft Post; audit_escalation →
   Telegram booking ping; platform_focus → scheduler weight event.
2. outcome → feedback: executed drafts are tagged with the action so post
   metrics join back on the next sweep.
3. outcome → training: every execution persists a `datalake_actions`
   AnalyticsEvent — the outcome row for future model/style training,
   carried by the existing analytics_events export.

Runs after the insights sweep (same 6h cadence, +25min offset).
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text

from app.core.security import decrypt_token
from app.models import AnalyticsEvent, Post, SocialAccount
from app.services.telegram_api import TelegramAPIClient
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

TELEGRAM_CHAT = "-1004436942504"
SIGNALS_TYPE = "datalake_signals"
ACTIONS_TYPE = "datalake_actions"


async def _latest_signals(db) -> dict:
    ev = (
        await db.execute(
            select(AnalyticsEvent)
            .where(AnalyticsEvent.event_type == SIGNALS_TYPE)
            .order_by(AnalyticsEvent.occurred_at.desc())
            .limit(1))
    ).scalar_one_or_none()
    return (ev.meta_data or {}) if ev else {}


async def _already_executed(db, action_type: str, key: str) -> bool:
    """Dedup — skip if the same action key ran in the last 24h."""
    row = (
        await db.execute(
            text(
                "SELECT 1 FROM analytics_events WHERE event_type=:t"
                " AND meta_data->>'action_type'=:a AND meta_data->>'key'=:k"
                " AND occurred_at >= :s LIMIT 1"),
            {"t": ACTIONS_TYPE, "a": action_type, "k": key,
             "s": datetime.now(UTC) - timedelta(hours=24)})
    ).scalar_one_or_none()
    return row is not None


async def _exec_content_topic(db, team_id, action) -> dict:
    """content_topic → a draft Post tagged for the feedback join."""
    topic = action["topic"]
    if await _already_executed(db, "content_topic", topic):
        return {"skipped": "already drafted <24h"}
    platform = action.get("platform") or "linkedin"
    user_id = (
        await db.execute(
            text("SELECT user_id FROM posts WHERE team_id=:t"
                 " AND user_id IS NOT NULL LIMIT 1"),
            {"t": team_id})
    ).scalar_one_or_none()
    post = Post(
        team_id=team_id,
        user_id=user_id,
        content_text=f"[draft] {topic} — write from this pain point",
        status="draft",
        meta_data={
            "source": "datalake_action", "action_type": "content_topic",
            "topic": topic, "platform": platform,
            "reason": action.get("reason", ""),
        },
    )
    db.add(post)
    await db.flush()
    return {"post_id": str(post.id), "platform": platform}


async def _exec_audit_escalation(db, team_id, action) -> dict:
    """audit_escalation → Telegram booking ping with the emails."""
    emails = action.get("emails") or []
    key = ",".join(sorted(emails))[:200]
    if await _already_executed(db, "audit_escalation", key):
        return {"skipped": "already pinged <24h"}
    acc = (
        await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == "telegram",
                SocialAccount.status == "active").limit(1))
    ).scalar_one()
    enc = acc.meta_data.get("bot_token_enc")
    tok = decrypt_token(enc.encode() if isinstance(enc, str) else enc) \
        if enc else decrypt_token(acc.access_token_enc)
    await TelegramAPIClient(tok).send_message(
        TELEGRAM_CHAT,
        "📞 Audit requests pending booking:\n" + "\n".join(emails[:8]))
    return {"pinged": len(emails)}


def _exec_platform_focus(action) -> dict:
    """platform_focus → weight the scheduler's posting order."""
    return {"platform_focus": action.get("platform"), "weight": "boost"}


_EXECUTORS = {
    "content_topic": _exec_content_topic,
    "audit_escalation": _exec_audit_escalation,
    "platform_focus": _exec_platform_focus,
}


@celery_app.task(name="app.worker.tasks.datalake_executor.execute_actions")
def execute_actions() -> dict:
    return run_async(_run())


async def _run() -> dict:
    async with task_session() as db:
        signals = await _latest_signals(db)
        actions = signals.get("actions") or []
        if not signals or not actions:
            return {"skipped": "no signals"}
        team_id = uuid.UUID(signals["team_id"]) if isinstance(
            signals.get("team_id"), str) else signals["team_id"]
        results = []
        for action in actions:
            fn: Any = _EXECUTORS.get(action.get("type"))
            if not fn:
                continue
            key = str(action.get("topic") or action.get("platform") or
                      action.get("type"))[:200]
            try:
                out = await fn(db, team_id, action) \
                    if asyncio.iscoroutinefunction(fn) else fn(action)
            except Exception as e:
                out = {"error": str(e)[:120]}
            db.add(AnalyticsEvent(
                team_id=team_id, event_type=ACTIONS_TYPE,
                platform="datalake", occurred_at=datetime.now(UTC),
                meta_data={
                    "action_type": action["type"], "key": key,
                    "reason": action.get("reason", ""), "result": out}))
            results.append({"type": action["type"], **out})
        await db.commit()
        return {"executed": len(results), "results": results}
