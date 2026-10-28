"""Nextcloud Talk webhook bot — two-way SocialAuto commands from Talk.

Installed server-side via ``occ talk:bot:install``; Talk POSTs every message
in the bot's rooms here, signed with HMAC-SHA256 (X-Nextcloud-Talk-Random +
raw body, NEXTCLOUD_TALK_BOT_SECRET). Replies go back through the bot message
endpoint (nextcloud_talk.bot_reply).

Commands (message text, case-insensitive):
    help                 list commands
    status               accounts per platform + publish-queue counts
    heal                 run the session-heal sweep now
    digest               trigger the daily digest task
    retry <queue_id>     re-queue a failed publish-queue item
    skip <queue_id>      cancel a pending publish-queue item

Bot install (on omv):
    docker exec -u www-data nextcloud php occ talk:bot:install \
        SocialAuto <secret> http://192.168.1.23:8083/api/v1/talk-bot 2
(feature 2 = webhook; bot must then be added to the room:
 occ talk:bot:setup <bot_id> <room_token>)
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.queue import PublishQueue, QueueStatus
from app.models.social_account import SocialAccount
from app.services.nextcloud_talk import bot_reply, verify_bot_request

logger = logging.getLogger(__name__)

router = APIRouter()

_HELP = (
    "SocialAuto commands:\n"
    "• status — accounts + publish queue state\n"
    "• heal — run the session-heal sweep\n"
    "• digest — send the daily digest now\n"
    "• retry <queue_id> — re-queue a failed publish\n"
    "• skip <queue_id> — cancel a pending publish"
)


def _command(payload: dict) -> tuple[str, str | None, int | None]:
    """Extract (text, room_token, message_id) from an ActivityStreams payload."""
    if payload.get("type") != "Create":
        return "", None, None
    obj = payload.get("object") or {}
    target = payload.get("target") or {}
    try:
        content = json.loads(obj.get("content") or "{}")
        text = str(content.get("message") or "").strip()
    except (TypeError, ValueError):
        text = ""
    try:
        msg_id = int(obj.get("id"))
    except (TypeError, ValueError):
        msg_id = None
    # Room token is requester-supplied and lands in the reply URL — only
    # reply into the two configured rooms (whitelist = SSRF-safe constant).
    s = get_settings()
    allowed = {
        (getattr(s, "NEXTCLOUD_TALK_ROOM", "") or "").strip(),
        (getattr(s, "NEXTCLOUD_TALK_ROOM_ALERTS", "") or "").strip(),
    }
    allowed.discard("")
    room = str(target.get("id") or "").strip()
    return text, (room if room in allowed else None), msg_id


async def _status() -> str:
    async with async_session_maker() as db:
        rows = (
            await db.execute(
                select(SocialAccount.platform, SocialAccount.status,
                       func.count())
                .group_by(SocialAccount.platform, SocialAccount.status)
            )
        ).all()
        q = (
            await db.execute(
                select(PublishQueue.status, func.count())
                .group_by(PublishQueue.status)
            )
        ).all()
    lines = ["*Accounts:*"]
    for platform, status, n in sorted(rows):
        lines.append(f"  {platform}: {n} ({status})")
    lines.append("*Publish queue:*")
    for status, n in sorted(q, key=lambda r: str(r[0])):
        lines.append(f"  {status}: {n}")
    return "\n".join(lines) if rows or q else "No accounts yet."


async def _queue_action(queue_id: str, action: str) -> str:
    try:
        qid = uuid.UUID(queue_id)
    except ValueError:
        return f"`{queue_id}` is not a queue id"
    async with async_session_maker() as db:
        item = await db.get(PublishQueue, qid)
        if item is None:
            return f"Queue item `{queue_id}` not found"
        if action == "retry":
            if item.status != QueueStatus.FAILED:
                return f"Queue item is `{item.status}` — only failed items retry"
            item.status = QueueStatus.PENDING
            item.locked_at = None
            item.locked_by = None
            verb = "re-queued"
        else:
            if item.status in (QueueStatus.PROCESSING, QueueStatus.COMPLETED):
                return f"Queue item is `{item.status}` — can't skip that"
            item.status = QueueStatus.CANCELLED
            verb = "cancelled"
        await db.commit()
    return f"Queue item `{queue_id}` {verb}."


async def _run_command(text: str) -> str:
    parts = text.split()
    if not parts:
        return ""
    cmd = parts[0].lower().lstrip("/!")
    args = parts[1:]
    if cmd in ("help", "commands"):
        return _HELP
    if cmd == "status":
        return await _status()
    if cmd == "heal":
        from app.worker.celery_app import celery_app

        celery_app.send_task("app.worker.tasks.session_healer.heal_sessions")
        return "Session-heal sweep queued."
    if cmd == "digest":
        from app.worker.celery_app import celery_app

        celery_app.send_task(
            "app.worker.tasks.digest.send_daily_slack_digest",
            kwargs={"days": 1},
        )
        return "Daily digest queued."
    if cmd in ("retry", "skip") and args:
        return await _queue_action(args[0], cmd)
    return ""  # not a command — stay silent


@router.post("/talk-bot")
async def talk_bot_webhook(request: Request) -> dict:
    body = await request.body()
    random = request.headers.get("x-nextcloud-talk-random", "")
    signature = request.headers.get("x-nextcloud-talk-signature", "")
    if not verify_bot_request(random, body, signature):
        return {"ok": False}
    try:
        payload = json.loads(body)
    except ValueError:
        return {"ok": False}
    text, room, msg_id = _command(payload)
    if not text:
        return {"ok": True}
    try:
        reply = await _run_command(text)
    except Exception:  # noqa: BLE001
        logger.exception("Talk bot command failed")
        reply = "That command failed — check worker logs."
    if reply and room:
        await bot_reply(room, reply, reply_to=msg_id)
    return {"ok": True}
