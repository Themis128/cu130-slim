"""Daily Ombudsman (Συνήγορος του Πολίτη) complaint-status poll.

Fills the public tracking form on the adhoc browser bridge (email + ΚΠΑ),
extracts Κατάσταση / Αριθμός Πρωτοκόλλου, and posts a Telegram alert to the
Automation HQ channel when the status changes. Last status persists in the
`social_secrets` KV table so beat restarts don't re-alert.
"""
from __future__ import annotations

import json
import logging
import os

import httpx
from sqlalchemy import select

from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.models.social_secret import SocialSecret
from app.services.telegram_api import TelegramAPIClient
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

BRIDGE = os.environ.get("ADHOC_BRIDGE_URL", "http://browser-novnc-adhoc:9224")
TRACK_URL = "https://www.synigoros.gr/el/anafora/parakolouthisi-anaforas"
MAIL = os.environ.get("SYNIGOROS_MAIL", "baltzakis.themis@gmail.com")
KPA = os.environ.get("SYNIGOROS_KPA", "202610260029")
CHAT_ID = os.environ.get("SYNIGOROS_CHAT_ID", "-1004436942504")
STATE_KEY = "synigoros_last_status"


async def _bridge_call(client: httpx.AsyncClient, path: str, data: dict, tries: int = 3) -> dict:
    for i in range(tries):
        try:
            r = await client.post(f"{BRIDGE}{path}", json=data, timeout=45)
            if r.status_code == 409 or (r.status_code == 500 and "context was destroyed" in r.text):
                if i < tries - 1:
                    import asyncio
                    await asyncio.sleep(15)
                    continue
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError):
            if i < tries - 1:
                import asyncio
                await asyncio.sleep(15)
                continue
            raise
    return {}


async def _ev(client: httpx.AsyncClient, expr: str):
    return (await _bridge_call(client, "/session/evaluate", {"expression": expr})).get("result")


async def _poll() -> dict:
    """Drive the bridge through identify + status extraction."""
    import asyncio

    async with httpx.AsyncClient() as client:
        try:
            await _bridge_call(client, "/session/close", {})
        except httpx.HTTPError:
            pass
        await _bridge_call(
            client, "/session/start", {"platform": "reddit", "interactive": False, "force": True}
        )
        await asyncio.sleep(4)
        await _bridge_call(client, "/session/navigate", {"url": TRACK_URL})

        for _ in range(10):
            if await _ev(client, "!!document.getElementById('webMail')") or await _ev(
                client, "document.body.innerText.includes('Κατάσταση')"
            ):
                break
            await asyncio.sleep(3)
        else:
            raise RuntimeError("tracking page never rendered")

        if await _ev(client, "!!document.getElementById('webMail')"):
            await _ev(
                client,
                "(()=>{const m=document.getElementById('webMail');"
                f"m.value='{MAIL}';m.dispatchEvent(new Event('input',{{bubbles:true}}));"
                f"const c=document.getElementById('webCode');c.value='{KPA}';"
                "c.dispatchEvent(new Event('input',{bubbles:true}));return 1})()",
            )
            await _bridge_call(client, "/session/click", {"selector": "button"})
            await asyncio.sleep(14)
            if await _ev(client, "document.body.innerText.includes('Δεν έγινε ταυτοποίηση')"):
                raise RuntimeError("identify failed — wrong mail/ΚΠΑ pair")

        raw = await _ev(
            client,
            """(()=>{const t=document.body.innerText;
            const grab=(re)=>{const m=t.match(re);return m?m[1].trim():''};
            return JSON.stringify({
              status: grab(/Κατάσταση\\s*\\n([^\\n]+)/) || grab(/(Αναμονή[^\\n]*|Επεξεργασία[^\\n]*|Ολοκληρώθηκε[^\\n]*)/),
              proto:  grab(/Αριθμός Πρωτοκόλλου\\s*\\n?\\s*(\\d+)/),
              kpa:    grab(/Κωδικός Παρακολούθησης Αναφοράς\\s*\\n?\\s*(\\d+)/)
            })})()""",
        )
        return json.loads(raw) if raw else {}


async def _notify(status: str, proto: str) -> None:
    async with task_session() as db:
        acc = (
            await db.execute(
                select(SocialAccount)
                .where(SocialAccount.platform == "telegram", SocialAccount.status == "active")
                .limit(1)
            )
        ).scalar_one_or_none()
        if not acc:
            return
        md = acc.meta_data or {}
        enc = md.get("bot_token_enc")
        tok = (
            decrypt_token(enc.encode() if isinstance(enc, str) else enc)
            if enc
            else decrypt_token(acc.access_token_enc)
        )
        await TelegramAPIClient(tok).send_message(
            CHAT_ID, f"📋 Synigoros — status changed:\n{status} (πρωτ. {proto})"
        )


async def _run() -> str:
    result = await _poll()
    status = result.get("status", "")
    if not status:
        return "no-status-extracted"

    async with task_session() as db:
        row = (
            await db.execute(select(SocialSecret).where(SocialSecret.key == STATE_KEY))
        ).scalars().first()
        last = row.value if row else None
        if status == last:
            return "unchanged"
        if row:
            row.value = status
        else:
            db.add(SocialSecret(key=STATE_KEY, value=status, description="Last Synigoros poll status"))
        await db.commit()

    await _notify(status, result.get("proto", ""))
    return "notified"


@celery_app.task(name="app.worker.tasks.synigoros_status.poll_synigoros")
def poll_synigoros() -> str:
    """Daily poll of the Ombudsman complaint status → Telegram on change."""
    try:
        return run_async(_run())
    except Exception as exc:  # noqa: BLE001 — beat must not crash-loop
        logger.warning("synigoros poll failed: %s", exc)
        return f"error: {exc}"
