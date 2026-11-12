#!/usr/bin/env python3
"""report_delivery_watch.py — detect MISSING scheduled report emails.

report-watch.py alerts when a NEW report contains failures; this watcher
covers the other half of the contract: reports that were SUPPOSED to arrive
and didn't. For every expected report it checks the tbaltzakis@cloudless.gr
INBOX over IMAP, and on a miss it (a) auto-resends once per day via Celery
`send_task`, and (b) TG-alerts #ops if the report is still missing on a
later pass.

Expected-report registry is derived from the live beat schedule in
social-automation/backend/app/worker/celery_app.py + the n8n daily digest
workflow — update REPORTS when a beat entry is added/removed.

Usage:
  report_delivery_watch.py            # check + auto-resend once + alert
  report_delivery_watch.py --status   # print table, no resend/alert
  report_delivery_watch.py --resend   # force resend pass for all missing
"""

from __future__ import annotations

import argparse
import email
import imaplib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from email.header import decode_header
from pathlib import Path
from zoneinfo import ZoneInfo

HOME = Path.home()
REPO = HOME / "cu130-slim"
REPO_ENV = REPO / ".env"
STATE = HOME / ".local/state/report-delivery-watch/state.json"
LOG = HOME / "logs/report-delivery-watch.log"
TZ = ZoneInfo("Europe/Athens")
IMAP_HOST = "192.168.1.130"
IMAP_PORT = 993
MAIL_USER = "tbaltzakis@cloudless.gr"
WORKER_CONTAINER = "social-worker-default"

# ── expected reports ──────────────────────────────────────────────────────
# kind "count": `pattern` must match >= expected_count messages since local
#   midnight (expected_count may be a callable of the local date).
# kind "fresh":  newest matching message must be <= max_age_days old.
# resend: celery task + kwargs invoked via send_task inside the worker
#   container when the report is missing and no resend was attempted today.

def _socialauto_daily_count(d: datetime) -> int:
    # n8n daily digest (09:00) + Monday weekly rollup + 1st-of-month rollup
    # all share the "your SocialAuto daily" subject family.
    return 1 + (1 if d.weekday() == 0 else 0) + (1 if d.day == 1 else 0)


REPORTS = [
    {
        "key": "socialauto_daily",
        "label": "SocialAuto daily digest (n8n)",
        "kind": "count",
        "pattern": r"your SocialAuto daily",
        "expected": _socialauto_daily_count,
        "due_hour": 10,          # n8n cron 09:00 + margin
        "resend": ("app.worker.tasks.digest.send_daily_slack_digest",
                   {"days": 1, "post_to_slack": True, "post_to_email": True}),
    },
    {
        "key": "strategy_brief",
        "label": "Daily strategy brief",
        "kind": "count",
        "pattern": r"\[SocialAuto\] Strategy brief",
        "expected": 1,
        "due_hour": 9,
        "resend": ("app.worker.tasks.notebook_reports.run_notebook_report",
                   {"notebook": "daily_strategy_brief",
                    "parameters": {"insight_days": 30},
                    "send": True, "fallback_to_code": True}),
    },
    {
        "key": "ops_health",
        "label": "Daily ops health",
        "kind": "count",
        "pattern": r"\[SocialAuto\] Ops health",
        "expected": 1,
        "due_hour": 10,
        "resend": ("app.worker.tasks.notebook_reports.run_notebook_report",
                   {"notebook": "ops_health", "send": True,
                    "fallback_to_code": False}),
    },
    {
        "key": "monetization",
        "label": "Daily monetization report",
        "kind": "count",
        "pattern": r"SocialAuto monetization report",
        "expected": 1,
        "due_hour": 12,
        "resend": ("app.worker.tasks.notebook_reports.run_notebook_report",
                   {"notebook": "monetization_daily",
                    "parameters": {"insight_days": 30, "execute": True},
                    "send": True, "fallback_to_code": True}),
    },
    {
        "key": "promo_scan",
        "label": "Daily free-promo scan",
        "kind": "count",
        "pattern": r"Free-promo scan",
        "expected": 1,
        "due_hour": 8,
        "resend": ("app.worker.tasks.promo_scan.run_promo_scan",
                   {"send": True}),
    },
    {
        "key": "linkedin_ads",
        "label": "Daily LinkedIn ad report",
        "kind": "count",
        "pattern": r"LinkedIn ad report",
        "expected": 1,
        "due_hour": 11,
        "resend": ("app.worker.tasks.linkedin_ads_report.send_linkedin_ads_report",
                   {}),
    },
    {
        "key": "omv_health",
        "label": "OMV daily health / maintenance mail",
        "kind": "fresh",
        "pattern": r"\[omv\]|pending-config|HEARTBEAT",
        "max_age_days": 3,
        "resend": None,
    },
]


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{datetime.now(TZ):%Y-%m-%d %H:%M %Z} {msg}\n")


def env(name: str) -> str:
    if not REPO_ENV.exists():
        return ""
    for line in REPO_ENV.read_text(errors="replace").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def decode_str(v: str) -> str:
    out = []
    for chunk, enc in decode_header(v or ""):
        if isinstance(chunk, bytes):
            out.append(chunk.decode(enc or "utf-8", "replace"))
        else:
            out.append(chunk)
    return "".join(out)


def tg_send(text: str) -> None:
    """Post to Telegram #ops via the repo tg_alert helper when available."""
    helper = REPO / ".devin/skills/telegram-alert-ops/scripts/tg_alert.py"
    if not helper.exists():
        log("tg_alert.py missing — alert not sent")
        return
    try:
        subprocess.run(
            [sys.executable, str(helper), "ops", text[:3900]],
            timeout=30, capture_output=True,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"tg send failed: {exc}")


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:  # noqa: BLE001
            pass
    return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1))


def fetch_inbox(password: str, days_back: int = 10) -> list[dict]:
    """Return [{subject, sender, date}] for messages in the last N days."""
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(MAIL_USER, password)
    mail.select("INBOX")
    since = (datetime.now(TZ) - timedelta(days=days_back)).strftime("%d-%b-%Y")
    _, data = mail.search(None, "SINCE", since)
    ids = data[0].split()
    out = []
    for mid in ids[-400:]:  # cap — mailbox churn is modest
        _, md = mail.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
        if not md or not md[0]:
            continue
        msg = email.message_from_bytes(md[0][1])
        out.append({
            "subject": decode_str(msg.get("Subject", "")),
            "sender": decode_str(msg.get("From", "")),
            "date": msg.get("Date", ""),
        })
    mail.logout()
    return out


def resend(task: str, kwargs: dict) -> bool:
    """Queue a celery task inside the worker container via send_task."""
    code = (
        "from app.worker.celery_app import celery_app;"
        f"celery_app.send_task({task!r}, kwargs={kwargs!r});"
        "print('queued')"
    )
    try:
        r = subprocess.run(
            ["docker", "exec", WORKER_CONTAINER, "python", "-c", code],
            timeout=60, capture_output=True, text=True,
        )
        ok = "queued" in (r.stdout or "")
        log(f"resend {task} -> {'queued' if ok else (r.stderr or r.stdout)[:200]}")
        return ok
    except Exception as exc:  # noqa: BLE001
        log(f"resend {task} failed: {exc}")
        return False


def check(password: str, do_resend: bool, do_alert: bool) -> int:
    now = datetime.now(TZ)
    today = now.date()
    msgs = fetch_inbox(password)
    state = load_state()
    resent_today = set(state.get("resent", {}).get(today.isoformat(), []))
    alerted_today = set(state.get("alerted", {}).get(today.isoformat(), []))
    results = []
    missing = []

    for spec in REPORTS:
        pat = re.compile(spec["pattern"], re.I)
        hits = [m for m in msgs if pat.search(m["subject"])]

        if spec["kind"] == "count":
            if now.hour < spec.get("due_hour", 0):
                results.append((spec["key"], "not-due-yet", f"due ~{spec['due_hour']:02d}:00"))
                continue
            exp = spec["expected"]
            need = exp(today) if callable(exp) else exp
            got = sum(1 for m in hits
                      if _msg_is_today(m, today))
            if got >= need:
                results.append((spec["key"], "ok", f"{got}/{need}"))
            else:
                results.append((spec["key"], "MISSING", f"{got}/{need}"))
                missing.append((spec, got, need))
        else:  # fresh
            newest = _newest_date(hits)
            if newest is None:
                results.append((spec["key"], "MISSING", "no email ever"))
                missing.append((spec, 0, 1))
            else:
                age = (now.date() - newest).days
                if age <= spec["max_age_days"]:
                    results.append((spec["key"], "ok", f"{age}d old"))
                else:
                    results.append((spec["key"], "STALE", f"{age}d old"))
                    missing.append((spec, age, spec["max_age_days"]))

    # resend once per spec per day, then alert only if still missing
    for spec, got, need in missing:
        key = spec["key"]
        if do_resend and spec.get("resend") and key not in resent_today:
            task, kwargs = spec["resend"]
            if resend(task, kwargs):
                resent_today.add(key)
                results = [(k, "resend-queued" if k == key else s, d)
                           for k, s, d in results]
                continue
        if do_alert and key not in alerted_today:
            alerted_today.add(key)
            tg_send(
                f"report-delivery: MISSING {spec['label']} — "
                f"expected {need}, found {got}. "
                f"No matching email in tbaltzakis@cloudless.gr INBOX today."
            )

    state.setdefault("resent", {})[today.isoformat()] = sorted(resent_today)
    state.setdefault("alerted", {})[today.isoformat()] = sorted(alerted_today)
    # prune state older than 14 days
    for sect in ("resent", "alerted"):
        state[sect] = {k: v for k, v in state[sect].items()
                       if (today - datetime.fromisoformat(k).date()).days <= 14}
    save_state(state)

    w = max((len(r[0]) for r in results), default=10)
    for key, status, detail in results:
        print(f"{key:<{w}}  {status:<14} {detail}")
    return 1 if any(s.startswith("MISSING") or s == "STALE"
                    for _, s, _ in results) else 0


def _msg_is_today(m: dict, today) -> bool:
    """Date-header parse best-effort: the IMAP query is already scoped to the
    last 10 days, so when the Date header won't parse we count the message
    (false positives are unlikely for daily-cadence reports)."""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(m["date"])
        return dt.astimezone(TZ).date() == today
    except Exception:  # noqa: BLE001
        return True


def _newest_date(hits: list[dict]):
    from email.utils import parsedate_to_datetime
    best = None
    for m in hits:
        try:
            d = parsedate_to_datetime(m["date"]).date()
            best = d if best is None or d > best else best
        except Exception:  # noqa: BLE001
            continue
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true",
                    help="print table only — no resend, no alerts")
    ap.add_argument("--resend", action="store_true",
                    help="force resend pass for missing reports")
    args = ap.parse_args()

    password = env("MAIL_TBALTZAKIS_PASSWORD") or env("MAILBOX_PASSWORD")
    if not password:
        log("no mailbox password in .env — skip")
        return 0

    do_resend = args.resend or not args.status
    do_alert = not args.status
    rc = check(password, do_resend=do_resend, do_alert=do_alert)
    log(f"check done rc={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
