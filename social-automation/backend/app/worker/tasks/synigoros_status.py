"""synigoros_status — daily Ombudsman complaint-status poll.

Navigates the tracking page on browser-novnc-adhoc, identifies with
email+ΚΠΑ, extracts Κατάσταση, persists it as an AnalyticsEvent, and pings
the Cloudless — Automation HQ Telegram channel when the status changes.

Skill doc: .devin/skills/synigoros-ops/SKILL.md
"""

import asyncio
import json
import logging
import os
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

BRIDGE = os.environ.get("ADHOC_BRIDGE_URL", os.environ.get("BROWSER_BRIDGE_URL", "http://stack-ops:9223"))
TRACK_URL = "https://www.synigoros.gr/el/anafora/parakolouthisi-anaforas"
MAIL = os.environ.get("SYNIGOROS_MAIL", "")
KPA = os.environ.get("SYNIGOROS_KPA", "")
TELEGRAM_CHAT = "-1004436942504"  # Cloudless — Automation HQ
EVENT_TYPE = "synigoros_status"
PLATFORM = "synigoros"


def _bridge(path: str, data: dict, tries: int = 3) -> dict:
    for i in range(tries):
        r = httpx.post(f"{BRIDGE}{path}", json=data, timeout=40)
        if r.status_code == 409 or (r.status_code == 500 and "context was destroyed" in r.text):
            time.sleep(15)
            continue
        r.raise_for_status()
        return r.json()
    return {}


def _ev(expr: str):
    return _bridge("/session/evaluate", {"expression": expr}).get("result")


def _poll() -> dict:
    # boot the shared browser (any platform arg) — unforced: a forced start
    # kills a live publishing/login session on the shared bridge
    _bridge("/session/start", {"platform": "reddit", "interactive": False})
    try:
        return _poll_inner()
    finally:
        try:
            _bridge("/session/close", {})
        except Exception:
            pass


def _poll_inner() -> dict:
    time.sleep(4)
    _bridge("/session/navigate", {"url": TRACK_URL})
    for _ in range(10):
        if _ev("!!document.getElementById('webMail')") or _ev("document.body.innerText.includes('Κατάσταση')"):
            break
        time.sleep(3)
    else:
        raise RuntimeError("tracking page never rendered")
    if _ev("!!document.getElementById('webMail')"):
        _ev(
            f"(()=>{{const m=document.getElementById('webMail');m.value={json.dumps(MAIL)};"
            "m.dispatchEvent(new Event('input',{bubbles:true}));"
            f"const c=document.getElementById('webCode');c.value={json.dumps(KPA)};"
            "c.dispatchEvent(new Event('input',{bubbles:true}));return 1})()"
        )
        _bridge("/session/click", {"selector": "button", "text": "Ταυτοποίηση"})
        # bounded poll for the status view or the explicit failure text —
        # a fixed sleep extracts empty fields on slow identification
        failed = False
        for _ in range(10):
            time.sleep(3)
            if _ev("document.body.innerText.includes('Κατάσταση')"):
                break
            if _ev("document.body.innerText.includes('Δεν έγινε ταυτοποίηση')"):
                failed = True
                break
        if failed:
            raise RuntimeError("identify failed — wrong mail/ΚΠΑ pair")
    out = _ev(
        """(()=>{const t=document.body.innerText;
        const grab=(re)=>{const m=t.match(re);return m?m[1].trim():''};
        return JSON.stringify({
          status: grab(/(Αναμονή[^\\n]*|Επεξεργασία[^\\n]*|Ολοκληρώθηκε[^\\n]*|Σε εξέλιξη[^\\n]*|Σε επεξεργασία[^\\n]*)/) ||
                  grab(/Κατάσταση\\s*\\n([^\\n]+)/),
          proto: grab(/Αριθμός Πρωτοκόλλου\\s*\\n?\\s*(\\d+)/),
          kpa:   grab(/Κωδικός Παρακολούθησης Αναφοράς\\s*\\n?\\s*(\\d+)/)
        })})()"""
    )
    result = json.loads(out)
    if result.get("kpa") and result["kpa"] != KPA:
        raise RuntimeError(f"cached session showed ΚΠΑ {result['kpa']}, expected {KPA} — reset required")
    if not result.get("status"):
        raise RuntimeError("status extraction empty — challenge or transition state")
    return result


@celery_app.task(name="app.worker.tasks.synigoros_status.poll_synigoros")
def poll_synigoros() -> dict:
    return run_async(_run())


async def _run() -> dict:
    if not MAIL or not KPA:
        logger.warning("synigoros_status: SYNIGOROS_MAIL/SYNIGOROS_KPA unset — skipping poll")
        return {"skipped": "credentials unset"}
    result = await asyncio.to_thread(_poll)
    status = result.get("status", "")
    async with task_session() as db:
        last = (
            await db.execute(select(AnalyticsEvent).where(AnalyticsEvent.event_type == EVENT_TYPE).order_by(AnalyticsEvent.occurred_at.desc()).limit(1))
        ).scalar_one_or_none()
        acc = (
            await db.execute(
                select(SocialAccount)
                .where(
                    SocialAccount.platform == "telegram",
                    SocialAccount.status == "active",
                )
                .limit(1)
            )
        ).scalar_one()
        team_id = last.team_id if last else acc.team_id
        changed = bool(status) and (not last or last.meta_data.get("status") != status)
        db.add(
            AnalyticsEvent(
                team_id=team_id,
                event_type=EVENT_TYPE,
                platform=PLATFORM,
                occurred_at=datetime.now(UTC),
                meta_data=result,
            )
        )
        if changed:
            enc = acc.meta_data.get("bot_token_enc")
            tok = decrypt_token(enc.encode() if isinstance(enc, str) else enc) if enc else decrypt_token(acc.access_token_enc)
            await TelegramAPIClient(tok).send_message(TELEGRAM_CHAT, f"📋 Synigoros — status changed:\n{status}")
            result["telegram_notified"] = True
    return result
