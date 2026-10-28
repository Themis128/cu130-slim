"""mastersheet_watch — poll the Founders' Circle Notion mastersheet for changes.

Renders the JS page on the adhoc browser bridge, extracts the content
(weeks/recordings/slides/homework), diffs against the last persisted snapshot,
Telegram-notifies on change, and logs the new data as datalake events
(coaching_action) so the insights loop adopts it.
"""

import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from app.core.security import decrypt_token
from app.models import AnalyticsEvent, SocialAccount
from app.services.telegram_api import TelegramAPIClient
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

BRIDGE = "http://browser-novnc-adhoc:9224"
SHEET_URL = ("https://sedate-ulna-278.notion.site/"
             "Learners-Founders-Circle-Mastersheet-3ff172ca8fc080b0ac85e0ea6b2673e9")
TELEGRAM_CHAT = "-1004436942504"
EVENT_TYPE = "mastersheet_snapshot"
PLATFORM = "founders_circle"


def _bridge(path, data, tries=3):
    for i in range(tries):
        try:
            r = httpx.post(f"{BRIDGE}{path}", json=data, timeout=45)
            if r.status_code == 409 or (r.status_code == 500 and "context was destroyed" in r.text):
                time.sleep(15); continue
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError:
            if i == tries - 1:
                raise
    return {}


def _ev(expr):
    return _bridge("/session/evaluate", {"expression": expr}).get("result")


def _fetch() -> dict:
    try:
        _bridge("/session/close", {})
    except Exception:
        pass
    _bridge("/session/start", {"platform": "reddit", "interactive": False, "force": True})
    time.sleep(4)
    _bridge("/session/navigate", {"url": SHEET_URL})
    for _ in range(8):
        if _ev("document.body.innerText.includes('Mastersheet')"):
            break
        time.sleep(4)
    else:
        raise RuntimeError("mastersheet never rendered")
    time.sleep(4)  # let the table lazy-load
    text = _ev("document.body.innerText") or ""
    return {"text": text[:8000], "hash": hashlib.sha256(text.encode()).hexdigest()}


async def _notify(db, text: str) -> None:
    acc = (
        await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == "telegram",
                SocialAccount.status == "active").limit(1))
    ).scalar_one()
    enc = acc.meta_data.get("bot_token_enc")
    tok = decrypt_token(enc.encode() if isinstance(enc, str) else enc) \
        if enc else decrypt_token(acc.access_token_enc)
    await TelegramAPIClient(tok).send_message(TELEGRAM_CHAT, text)


@celery_app.task(name="app.worker.tasks.mastersheet_watch.watch_mastersheet")
def watch_mastersheet() -> dict:
    return run_async(_run())


async def _run() -> dict:
    snap = await asyncio.to_thread(_fetch)
    async with task_session() as db:
        last = (
            await db.execute(
                select(AnalyticsEvent)
                .where(AnalyticsEvent.event_type == EVENT_TYPE)
                .order_by(AnalyticsEvent.occurred_at.desc())
                .limit(1))
        ).scalar_one_or_none()
        changed = not last or last.meta_data.get("hash") != snap["hash"]
        acc = (
            await db.execute(
                select(SocialAccount).where(
                    SocialAccount.platform == "telegram",
                    SocialAccount.status == "active").limit(1))
        ).scalar_one()
        team_id = last.team_id if last else acc.team_id
        db.add(AnalyticsEvent(
            team_id=team_id, event_type=EVENT_TYPE, platform=PLATFORM,
            occurred_at=datetime.now(UTC), meta_data=snap))
        if changed:
            db.add(AnalyticsEvent(
                team_id=team_id, event_type="coaching_action", platform=PLATFORM,
                occurred_at=datetime.now(UTC),
                meta_data={"source": "mastersheet_watch", "topic": "mastersheet update",
                           "content_head": snap["text"][:1500]}))
            await _notify(db, "📚 Founders' Circle mastersheet changed — new content landed → datalake")
            snap["telegram_notified"] = True
    return {"changed": changed, "hash": snap["hash"][:12]}
