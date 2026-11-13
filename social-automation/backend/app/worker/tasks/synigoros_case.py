"""synigoros_case — Ombudsman/DYPA/SEPE case follow-ups + reply watch.

Platform port of scripts/synigoros-monitor/{admin-followups,reply-watch,
check-replies}. Two Celery beat tasks:

- run_synigoros_followups (hourly :07) — evaluates
  backend/scripts/synigoros_case/followups.json: sends each due email via
  the omv-ha SMTP relay, fires tgOnly Telegram reminders, and applies the
  conditional gates (requireStatus vs the latest synigoros_status
  AnalyticsEvent, skipIfRepliedFrom dual-lane inbox check). Sent/skipped
  markers live in Redis so workers share state.
- watch_synigoros_replies (every 15 min) — scans Gmail
  (baltzakis.themis@gmail.com via the OAuth chain in synigoros_status) and
  the cloudless.gr IMAP mailbox for new human mail from the case
  authorities and alerts via send_telegram_alert("ops", ...).

Schedule + bodies: backend/scripts/synigoros_case/ (mounted read-only at
/app/scripts/synigoros_case in workers). The WSL scripts/synigoros-monitor
lane reads the same followups.json during the parallel-run migration —
markers are NOT shared between lanes, so an event due in both could
double-send; keep one lane authoritative per event window (see
.devin/BACKLOG.md).
"""

from __future__ import annotations

import asyncio
import imaplib
import json
import logging
import os
import re
import smtplib
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, time
from email.header import Header, decode_header
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import AnalyticsEvent, SocialAccount
from app.services.slack_notifications import send_telegram_alert
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app
from app.worker.tasks.synigoros_status import _env_file, _gmail_access_token

logger = logging.getLogger(__name__)

CASE_DIR = Path(os.environ.get("SYNIGOROS_CASE_DIR") or Path(__file__).resolve().parents[3] / "scripts" / "synigoros_case")
FOLLOWUPS_FILE = CASE_DIR / "followups.json"
WATCH_FILE = CASE_DIR / "reply-watch.json"

STATUS_EVENT_TYPE = "synigoros_status"
ATHENS = ZoneInfo("Europe/Athens")
DUE_HOUR = 8  # 08:00 Athens on sendAt — same contract as admin-followups.js

NOISE = re.compile(
    r"(κωδικ|οτπ|otp|επαληθευ|ταυτοποίηση|επιβεβαίωση σύνδεσης|"
    r"password|κωδικός|2fa|dump|bot|has new sign-in|"
    r"new sign-in|security alert|φόρμα αποστολής|υπενθύμιση|"
    r"ενημέρωση|επιτυχής)",
    re.I,
)
AUTO_SENDER = re.compile(r"(noreply|no-?reply|donotreply|no_reply)", re.I)

SENT_KEY = "synigoros-case:sent"          # hash: event_id -> iso ts | "skipped: ..."
SEEN_KEY = "synigoros-case:reply-seen"    # set of lane:uid keys already handled
SEEN_PRIMED_KEY = "synigoros-case:reply-seen-primed"
MAX_ALERTS_PER_RUN = 10


def _cfg(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    if v:
        return v
    return (_env_file().get(key) or default)


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — missing/malformed config must not kill the sweep
        logger.warning("synigoros_case: cannot read %s: %s", path, e)
        return default


async def _redis():
    import redis.asyncio as aioredis

    from app.core.config import get_settings

    return aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)


# ── Gates ────────────────────────────────────────────────────────────────────


async def _record(event_type: str, meta: dict) -> None:
    """Observable-output contract for workforce_audit — one row per sweep."""
    try:
        async with task_session() as db:
            acc = (
                await db.execute(
                    select(SocialAccount).where(SocialAccount.platform == "telegram").limit(1)
                )
            ).scalar_one_or_none()
            if not acc:
                return
            db.add(
                AnalyticsEvent(
                    team_id=acc.team_id,
                    event_type=event_type,
                    platform="synigoros",
                    occurred_at=datetime.now(UTC),
                    meta_data=meta,
                )
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.debug("synigoros_case: event record failed", exc_info=True)


async def _latest_status() -> str | None:
    """Confirmed status from the latest synigoros_status AnalyticsEvent."""
    async with task_session() as db:
        last = (
            await db.execute(
                select(AnalyticsEvent)
                .where(AnalyticsEvent.event_type == STATUS_EVENT_TYPE)
                .order_by(AnalyticsEvent.occurred_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if not last:
        return None
    meta = last.meta_data or {}
    return meta.get("status") or meta.get("raw_status")


def _dec(s: str) -> str:
    if not s:
        return ""
    return "".join(
        p.decode(c or "utf-8", errors="replace") if isinstance(p, bytes) else p
        for p, c in decode_header(s)
    )


def _imap_since(since: str) -> str:
    mon = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
           7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
    y, m, d = (int(x) for x in since.split("-"))
    return f"{d:02d}-{mon[m]}-{y}"


def _gmail_get(path: str) -> dict:
    token = _gmail_access_token()
    req = urllib.request.Request(
        f"https://gmail.googleapis.com/gmail/v1/users/me/{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        return json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            # refresh once and retry
            req = urllib.request.Request(
                f"https://gmail.googleapis.com/gmail/v1/users/me/{path}",
                headers={"Authorization": f"Bearer {_gmail_access_token()}"},
            )
            return json.load(urllib.request.urlopen(req, timeout=30))
        raise


def _interesting(frm: str, subj: str, require: re.Pattern | None) -> bool:
    if AUTO_SENDER.search(frm or "") or NOISE.search(subj or ""):
        return False
    return not (require and not require.search(subj or ""))


def _imap_hits(domain: str, since: str, require: re.Pattern | None) -> list[dict]:
    user = _cfg("IMAP_USER") or _cfg("SMTP_USER") or "tbaltzakis@cloudless.gr"
    pw = _cfg("IMAP_PASSWORD") or _cfg("SMTP_PASSWORD")
    if not pw:
        return []
    host = _cfg("IMAP_HOST", "192.168.1.130")
    port = int(_cfg("IMAP_PORT", "993"))
    out = []
    mail = imaplib.IMAP4_SSL(host, port)
    try:
        mail.login(user, pw)
        mail.select("INBOX")
        _, data = mail.uid("SEARCH", None, "FROM", f'"{domain}"', "SINCE", _imap_since(since))
        for uid in (data[0].split() if data and data[0] else [])[-20:]:
            _, msg = mail.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            raw = ""
            for part in msg or []:
                if isinstance(part, tuple) and len(part) > 1 and isinstance(part[1], bytes):
                    raw += part[1].decode("utf-8", "replace")
            frm = re.search(r"^From:\s*(.+)$", raw, re.M)
            sub = re.search(r"^Subject:\s*(.+)$", raw, re.M)
            dat = re.search(r"^Date:\s*(.+)$", raw, re.M)
            f, s = _dec(frm.group(1).strip()) if frm else "", _dec(sub.group(1).strip()) if sub else ""
            if _interesting(f, s, require):
                out.append({"lane": "cloudless", "from": f[:120], "subject": s[:160],
                            "date": _dec(dat.group(1).strip())[:60] if dat else ""})
    finally:
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass
    return out


def _gmail_hits(domain: str, since: str, require: re.Pattern | None) -> list[dict]:
    q = urllib.parse.quote(f"from:{domain} after:{since.replace('-', '/')}")
    res = _gmail_get(f"messages?q={q}&maxResults=50")
    out = []
    for m in res.get("messages", []):
        full = _gmail_get(
            f"messages/{m['id']}?format=metadata"
            "&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date"
        )
        h = {x["name"].lower(): x["value"] for x in full.get("payload", {}).get("headers", [])}
        f, s = h.get("from", ""), h.get("subject", "")
        if _interesting(f, s, require):
            out.append({"lane": "gmail", "from": f[:120], "subject": s[:160],
                        "date": h.get("date", "")[:60]})
    return out


def _replied(domain: str, since: str, require_src: str | None) -> tuple[bool, dict | None, str | None]:
    """Dual-lane reply check for the skip gate. Fail-open: lane errors are
    returned as `error` and the caller sends anyway."""
    require = re.compile(require_src, re.I) if require_src else None
    matches, errors = [], []
    for lane_fn in (_imap_hits, _gmail_hits):
        try:
            matches.extend(lane_fn(domain, since, require))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{lane_fn.__name__}: {e}")
    if matches:
        return True, matches[0], None
    return False, None, "; ".join(errors) or None


# ── Follow-up sweep ──────────────────────────────────────────────────────────


def _smtp_send(to: list[str], subject: str, body: str) -> None:
    host = _cfg("SMTP_HOST") or _cfg("SMTP_RELAY_HOST") or "smtp.resend.com"
    port = int(_cfg("SMTP_PORT") or _cfg("SMTP_RELAY_PORT") or "587")
    user = _cfg("SMTP_USER")
    pw = _cfg("SMTP_PASSWORD")
    frm = _cfg("SMTP_FROM") or user
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = frm
    msg["To"] = ", ".join(to)
    msg["Subject"] = Header(subject, "utf-8")
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(user, pw)
        smtp.sendmail(frm, to, msg.as_string())


def _read_body(path: Path) -> tuple[str, str]:
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^Subject:\s*(.+)$", raw, re.M)
    subject = m.group(1).strip() if m else path.stem
    body = re.sub(r"^Subject:.*\r?\n\s*\r?\n", "", raw, count=1)
    return subject, body.strip()


def _event_due(ev: dict, now: datetime) -> bool:
    try:
        d = date.fromisoformat(ev["sendAt"])
    except (KeyError, ValueError):
        return False
    return now >= datetime.combine(d, time(DUE_HOUR, 0), tzinfo=ATHENS)


async def _run_followups() -> dict:
    sched = _load_json(FOLLOWUPS_FILE, {"events": []})
    events = sched.get("events", [])
    if not events:
        return {"skipped": "no followups.json"}

    r = await _redis()
    sent = await r.hgetall(SENT_KEY)
    now = datetime.now(ATHENS)
    results = []

    for ev in events:
        eid = ev.get("id")
        if not eid or eid in sent or not _event_due(ev, now):
            continue

        # Status gate — applies to tgOnly too. Fail-open when no status is
        # known yet (first deploy before the daily poll lands).
        if ev.get("requireStatus"):
            cur = await _latest_status()
            if cur and cur != ev["requireStatus"]:
                why = f"skipped: status={cur} (required {ev['requireStatus']})"
                await r.hset(SENT_KEY, eid, why)
                await send_telegram_alert("ops", f"⏭️ {eid} παραλείφθηκε — {why}")
                results.append({"id": eid, "result": "skipped-status"})
                continue

        if ev.get("tgOnly"):
            ok = await send_telegram_alert("ops", f"⏰ {ev.get('tgText', '')}")
            if ok:
                await r.hset(SENT_KEY, eid, datetime.now(UTC).isoformat())
                results.append({"id": eid, "result": "tg-sent"})
            else:
                results.append({"id": eid, "result": "tg-failed"})
            continue

        # Reply gate — skip the email if the authority already answered.
        if ev.get("skipIfRepliedFrom"):
            replied, match, err = await asyncio.to_thread(
                _replied,
                ev["skipIfRepliedFrom"],
                ev.get("skipIfRepliedSince") or ev["sendAt"],
                ev.get("skipIfRepliedRequire"),
            )
            if replied:
                why = (f"skipped: reply from {ev['skipIfRepliedFrom']} since "
                       f"{ev.get('skipIfRepliedSince') or ev['sendAt']}")
                await r.hset(SENT_KEY, eid, why)
                subj = (match or {}).get("subject", "")
                await send_telegram_alert("ops", f"⏭️ {eid} παραλείφθηκε — {why} ({subj})")
                results.append({"id": eid, "result": "skipped-reply"})
                continue
            if err:
                logger.warning("synigoros_case %s: reply-check failed (sending anyway): %s", eid, err)

        try:
            subject, body = _read_body(CASE_DIR / ev["file"])
            await asyncio.to_thread(_smtp_send, ev["to"], subject, body)
        except Exception as e:  # noqa: BLE001
            logger.exception("synigoros_case %s send failed", eid)
            await send_telegram_alert("ops", f"⚠️ ΑΠΟΤΥΧΙΑ follow-up {eid}: {e}")
            results.append({"id": eid, "result": "send-failed", "error": str(e)[:200]})
            continue

        await r.hset(SENT_KEY, eid, datetime.now(UTC).isoformat())
        await send_telegram_alert(
            "ops",
            f"📮 {eid} ΕΣΤΑΛΗ → {', '.join(ev['to'])}\n«{subject}»\nπρογραμματισμένο: {ev['sendAt']}",
        )
        results.append({"id": eid, "result": "sent", "subject": subject[:80]})

    result = {"processed": len(results), "events": results}
    await _record("synigoros_case_followups", result)
    await r.aclose()
    return result


@celery_app.task(name="app.worker.tasks.synigoros_case.run_synigoros_followups")
def run_synigoros_followups() -> dict:
    return run_async(_run_followups())


# ── Reply watch ──────────────────────────────────────────────────────────────


def _parse_domains(raw) -> list[tuple[str, str | None]]:
    out = []
    for d in raw or []:
        if isinstance(d, str):
            out.append((d, None))
        elif isinstance(d, dict) and d.get("name"):
            out.append((d["name"], d.get("require")))
    return out


def _scan_replies_sync(domains: list[tuple[str, str | None]], since: str) -> list[dict]:
    """Both lanes; returns raw hits (pre-noise-filter keys included)."""
    fresh = []

    # IMAP lane — per-domain search, subject-require applied per domain.
    try:
        user = _cfg("IMAP_USER") or _cfg("SMTP_USER") or "tbaltzakis@cloudless.gr"
        pw = _cfg("IMAP_PASSWORD") or _cfg("SMTP_PASSWORD")
        if pw:
            host = _cfg("IMAP_HOST", "192.168.1.130")
            port = int(_cfg("IMAP_PORT", "993"))
            mail = imaplib.IMAP4_SSL(host, port)
            try:
                mail.login(user, pw)
                mail.select("INBOX")
                for dom, req_src in domains:
                    req = re.compile(req_src, re.I) if req_src else None
                    _, data = mail.uid("SEARCH", None, "FROM", f'"{dom}"', "SINCE", _imap_since(since))
                    for uid in data[0].split() if data and data[0] else []:
                        key = f"imap:{dom}:{uid.decode()}"
                        _, msg = mail.uid(
                            "FETCH", uid,
                            "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] BODY.PEEK[TEXT]<0.1200>)",
                        )
                        header_blob, body_blob = "", ""
                        for part in msg or []:
                            if not (isinstance(part, tuple) and len(part) > 1):
                                continue
                            meta = part[0].decode("utf-8", "replace") if isinstance(part[0], bytes) else str(part[0])
                            lit = part[1]
                            if not isinstance(lit, bytes):
                                continue
                            text = lit.decode("utf-8", "replace")
                            if "HEADER" in meta.upper():
                                header_blob = text
                            else:
                                body_blob = text
                        frm = re.search(r"^From:\s*(.+)$", header_blob, re.M)
                        sub = re.search(r"^Subject:\s*(.+)$", header_blob, re.M)
                        date_h = re.search(r"^Date:\s*(.+)$", header_blob, re.M)
                        snippet = re.sub(r"<[^>]+>", " ", body_blob)
                        snippet = re.sub(r"\s+", " ", snippet)[:160].strip()
                        subj = _dec(sub.group(1).strip()) if sub else ""
                        if req and not req.search(subj):
                            continue
                        fresh.append({
                            "key": key, "lane": "cloudless",
                            "from": _dec(frm.group(1).strip()) if frm else "",
                            "subject": subj,
                            "snippet": snippet,
                            "date": _dec(date_h.group(1).strip()) if date_h else "",
                            "auto": bool(AUTO_SENDER.search(_dec(frm.group(1).strip()) if frm else "")),
                        })
            finally:
                try:
                    mail.logout()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001
        logger.warning("synigoros_case reply-watch: IMAP lane failed: %s", e)

    # Gmail lane — union query, per-domain require applied client-side.
    try:
        q = "(" + " OR ".join(f"from:{d}" for d, _ in domains) + f") after:{since.replace('-', '/')}"
        res = _gmail_get(f"messages?q={urllib.parse.quote(q)}&maxResults=100")
        for m in res.get("messages", []):
            full = _gmail_get(
                f"messages/{m['id']}?format=metadata"
                "&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date"
            )
            headers = {h["name"].lower(): h["value"] for h in full.get("payload", {}).get("headers", [])}
            frm, subj = headers.get("from", ""), headers.get("subject", "")
            low = (frm + " " + subj).lower()
            req = None
            for d, r in domains:
                if d.lower() in low:
                    req = re.compile(r, re.I) if r else None
                    break
            if req and not req.search(subj):
                continue
            fresh.append({
                "key": f"gmail:{m['id']}", "lane": "gmail",
                "from": frm[:120], "subject": subj[:160],
                "snippet": (m.get("snippet") or "")[:160],
                "date": headers.get("date", "")[:60],
                "link": f"https://mail.google.com/mail/u/0/#all/{m.get('threadId', m['id'])}",
                "auto": bool(AUTO_SENDER.search(frm)),
            })
    except Exception as e:  # noqa: BLE001
        logger.warning("synigoros_case reply-watch: Gmail lane failed: %s", e)

    return fresh


async def _run_reply_watch() -> dict:
    cfg = _load_json(WATCH_FILE, {})
    domains = _parse_domains(cfg.get("domains")) or [
        ("synigoros.gr", None),
        (
            "dypa.gov.gr",
            "επανεξέταση|επιδότηση|επίδομα|ανεργ|ημερομίσθι|απόρριψη|απορριπτικ|"
            "πρωτοκόλλου|ΚΠΑ2|Μαρκοπούλου|000134573211|40173828|επαναπροώθηση|διαβίβαση",
        ),
        ("hli.gov.gr", None),
    ]
    since = cfg.get("since") or "2026-10-26"

    fresh = await asyncio.to_thread(_scan_replies_sync, domains, since)

    r = await _redis()
    try:
        primed = await r.exists(SEEN_PRIMED_KEY)
        keys = [m["key"] for m in fresh]
        new_keys = [k for k in keys if not await r.sismember(SEEN_KEY, k)]
        if keys:
            await r.sadd(SEEN_KEY, *keys)
        if not primed:
            # first run — record the baseline silently so historical mail
            # (automated status notifications etc.) never alerts
            await r.set(SEEN_PRIMED_KEY, "1")
            await _record("synigoros_case_reply_watch", {"primed": True, "seen": len(keys)})
            await r.aclose()
            return {"primed": True, "seen": len(keys)}

        interesting = [m for m in fresh
                       if m["key"] in new_keys
                       and not m.get("auto")
                       and not NOISE.search(m.get("subject") or "")]
        if not interesting:
            await _record("synigoros_case_reply_watch", {"new": len(new_keys), "alerted": 0})
            await r.aclose()
            return {"new": len(new_keys), "alerted": 0}

        lines = [f"📨 ΝΕΑ ΑΛΛΗΛΟΓΡΑΦΙΑ — {len(interesting)} μήνυμα(τα) από τις αρχές:"]
        for m in interesting[:MAX_ALERTS_PER_RUN]:
            lines.append(
                f"• [{m['lane']}] {m['subject'][:90]}\n"
                f"  {m['from'][:60]} — {m['date'][:40]}"
                + (f"\n  {m['snippet']}" if m.get("snippet") else "")
                + (f"\n  {m['link']}" if m.get("link") else "")
            )
        if len(interesting) > MAX_ALERTS_PER_RUN:
            lines.append(f"… και {len(interesting) - MAX_ALERTS_PER_RUN} ακόμη")
        await send_telegram_alert("ops", "\n".join(lines))
        result = {"new": len(new_keys), "alerted": len(interesting)}
        await _record("synigoros_case_reply_watch", result)
        await r.aclose()
        return result
    except Exception:
        await r.aclose()
        raise


@celery_app.task(name="app.worker.tasks.synigoros_case.watch_synigoros_replies")
def watch_synigoros_replies() -> dict:
    return run_async(_run_reply_watch())
