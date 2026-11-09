"""synigoros_status — daily Ombudsman complaint-status poll.

Navigates the tracking page on browser-novnc-adhoc, identifies with
email+ΚΠΑ, extracts Κατάσταση, persists it as an AnalyticsEvent, and pings
the Cloudless — Automation HQ Telegram channel when the status changes.

OTP step (added 2026-11-09): cold sessions get an otpCode screen after
Ταυτοποίηση; the 6-digit code is fetched from Gmail via the OAuth
refresh-token chain (GMAIL_REFRESH_TOKEN + GMAIL_OAUTH_CLIENT_ID/SECRET
read from /app/.env — no extra deps, stdlib urllib).

Skill doc: .devin/skills/synigoros-ops/SKILL.md
"""

import asyncio
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
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
ENV_FILE = "/app/.env"


def _env_file() -> dict:
    out = {}
    try:
        with open(ENV_FILE, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
                if m:
                    out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _gmail_access_token() -> str:
    env = _env_file()
    rt = env.get("GMAIL_REFRESH_TOKEN", "")
    cid = env.get("GMAIL_OAUTH_CLIENT_ID", "")
    csec = env.get("GMAIL_OAUTH_CLIENT_SECRET", "")
    if not (rt and cid and csec):
        raise RuntimeError("gmail oauth creds missing in /app/.env")
    data = urllib.parse.urlencode(
        {
            "client_id": cid,
            "client_secret": csec,
            "refresh_token": rt,
            "grant_type": "refresh_token",
        }
    ).encode()
    r = urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=data), timeout=20)
    return json.load(r)["access_token"]


def _gmail_otp(since_epoch: float, wait_s: int = 100) -> str | None:
    """Newest 6-digit OTP from noreply@synigoros.gr received after since_epoch."""
    deadline = time.time() + wait_s
    token = None
    while time.time() < deadline:
        if token is None:
            try:
                token = _gmail_access_token()
            except Exception as e:
                logger.warning("synigoros otp: token refresh failed: %s", e)
                time.sleep(10)
                continue
        try:
            q = urllib.parse.quote("from:synigoros.gr newer_than:30m")
            req = urllib.request.Request(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages?q={q}&maxResults=5",
                headers={"Authorization": f"Bearer {token}"},
            )
            msgs = json.load(urllib.request.urlopen(req, timeout=20)).get("messages", [])
        except Exception as e:
            logger.warning("synigoros otp: gmail list failed: %s", e)
            token = None
            time.sleep(10)
            continue
        for m in msgs:
            mid = m.get("id")
            if not mid:
                continue
            try:
                req = urllib.request.Request(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}?format=metadata&metadataHeaders=Subject",
                    headers={"Authorization": f"Bearer {token}"},
                )
                full = json.load(urllib.request.urlopen(req, timeout=20))
            except Exception as e:
                logger.warning("synigoros otp: gmail get %s failed: %s", mid, e)
                continue
            try:
                internal = int(full.get("internalDate", "0")) / 1000
            except (TypeError, ValueError):
                continue
            if internal < since_epoch - 60:
                continue
            snip = full.get("snippet", "")
            mm = re.search(r"(\d{6})", snip)
            if mm:
                return mm.group(1)
        time.sleep(10)
    return None


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
    start = _bridge("/session/start", {"platform": "reddit", "interactive": False})
    owns = not start.get("reused")  # a reused session belongs to another caller
    try:
        return _poll_inner()
    finally:
        # /session/stop is the real bridge endpoint (there is no
        # /session/close — a 404 here left the waiting session pinning the
        # bridge for five minutes); skip it when we reused someone else's.
        if owns:
            try:
                stopped = _bridge("/session/stop", {}, tries=1)
                if not stopped.get("status"):
                    logger.warning("synigoros_status: /session/stop no-op — session may pin the bridge ~5min")
            except Exception as e:
                logger.warning("synigoros_status: /session/stop failed: %s", e)


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
        identify_at = time.time()
        _ev(
            f"(()=>{{const m=document.getElementById('webMail');m.value={json.dumps(MAIL)};"
            "m.dispatchEvent(new Event('input',{bubbles:true}));"
            f"const c=document.getElementById('webCode');c.value={json.dumps(KPA)};"
            "c.dispatchEvent(new Event('input',{bubbles:true}));return 1})()"
        )
        _bridge("/session/click", {"selector": "button", "text": "Ταυτοποίηση"})
        # OTP step: the site emails a 6-digit code; fetch the newest one
        # from Gmail and submit it, then keep polling for the status view.
        otp_done = False
        failed = False
        for _ in range(30):
            time.sleep(3)
            if _ev("document.body.innerText.includes('Κατάσταση')"):
                break
            if _ev("document.body.innerText.includes('Δεν έγινε ταυτοποίηση')"):
                failed = True
                break
            if not otp_done and _ev("!!document.getElementById('otpCode')"):
                otp = _gmail_otp(identify_at)
                if not otp:
                    raise RuntimeError("OTP screen shown but no fresh Gmail code")
                _ev(
                    f"(()=>{{const o=document.getElementById('otpCode');o.value='{otp}';"
                    "o.dispatchEvent(new Event('input',{bubbles:true}));"
                    "o.dispatchEvent(new Event('change',{bubbles:true}));return 1})()"
                )
                _bridge("/session/click", {"selector": "button", "text": "Ταυτοποίηση"})
                otp_done = True
        if failed:
            raise RuntimeError("identify failed — wrong mail/ΚΠΑ pair")
    # the status block's fields fill asynchronously — a short settle keeps
    # proto/ΚΠΑ (and occasionally the status itself) from reading empty
    time.sleep(3)
    out = _ev(
        """(()=>{const t=document.body.innerText;
        const grab=(re)=>{const m=t.match(re);return m?m[1].trim():''};
        const ms=[...t.matchAll(/Κατάσταση\\s*\\n\\s*([^\\n]+)/g)];
        return JSON.stringify({
          status: (ms.length ? ms[ms.length-1][1].trim() : '') ||
                  grab(/(Αναμονή[^\\n]*|Προς Αποδοχή[^\\n]*|Επεξεργασία[^\\n]*|Ολοκληρώθηκε[^\\n]*|Σε εξέλιξη[^\\n]*|Σε επεξεργασία[^\\n]*)/),
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
    raw_status = result.get("status", "")
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
        # two-read confirmation — same contract as scripts/synigoros-monitor:
        # the Κατάσταση value oscillates between workflow states across polls
        # (real transitions + served snapshots), so a new value must repeat in
        # a second consecutive poll before it counts as the status.
        meta = last.meta_data if last else {}
        confirmed = meta.get("status") or raw_status
        candidate = None
        changed = bool(raw_status) and not last  # first-ever read arms + notifies
        if last and raw_status and raw_status != confirmed:
            if meta.get("candidate") == raw_status:
                confirmed, changed = raw_status, True  # 2nd consecutive sighting → promote
            else:
                candidate = raw_status  # first sighting — confirm on next run
        result.update({"status": confirmed, "raw_status": raw_status, "candidate": candidate})
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
            await TelegramAPIClient(tok).send_message(TELEGRAM_CHAT, f"📋 Synigoros — status changed:\n{confirmed}")
            result["telegram_notified"] = True
    return result
