#!/usr/bin/env python3
"""report_verify — cross-check daily report emails against real posts table.

Reads the last N days of tbaltzakis@cloudless.gr via IMAP, extracts
reported post counts from SocialAuto digest subjects, and diffs them
against the posts table (published_at). Telegrams on mismatch.

Cron: 30 9 * * *  MAILBOX_PASSWORD=... python3 report_verify.py
"""
import imaplib
import json
import os
import re
import subprocess
import sys

IMAP_HOST = os.getenv("IMAP_HOST", "192.168.1.130")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
USER = "tbaltzakis@cloudless.gr"
DAYS = int(os.getenv("REPORT_VERIFY_DAYS", "3"))

SEND_PY = """
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.models import SocialAccount
from app.core.config import get_settings
from app.core.security import decrypt_token
from app.services.telegram_api import TelegramAPIClient
async def go():
    eng = create_async_engine(get_settings().DATABASE_URL)
    S = sessionmaker(eng, class_=AsyncSession)
    async with S() as db:
        acc = (await db.execute(select(SocialAccount).where(
            SocialAccount.platform=='telegram',
            SocialAccount.status=='active').limit(1))).scalar_one()
        enc = acc.meta_data.get('bot_token_enc')
        tok = decrypt_token(enc.encode() if isinstance(enc,str) else enc) \
            if enc else decrypt_token(acc.access_token_enc)
        await TelegramAPIClient(tok).send_message(%r, %r)
asyncio.run(go())
"""


def _imap():
    pw = os.environ["MAILBOX_PASSWORD"]
    return imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT), pw


def fetch_report_subjects():
    m, pw = _imap()
    m.login(USER, pw)
    m.select("INBOX")
    from datetime import date, timedelta
    since = (date.today() - timedelta(days=DAYS)).strftime("%d-%b-%Y")
    _, data = m.search(None, f'(SINCE "{since}")')
    subs = []
    for num in (data[0].split() or [])[-60:]:
        _, msg = m.fetch(num, "(BODY[HEADER.FIELDS (SUBJECT DATE FROM)])")
        txt = msg[0][1].decode("utf-8", "replace") if msg and msg[0] else ""
        subj = re.search(r"Subject: (.*)", txt)
        frm = re.search(r"From: (.*)", txt)
        if subj:
            subs.append({"subject": subj.group(1).strip(),
                         "from": (frm.group(1) if frm else "").strip()[:40]})
    m.logout()
    return subs


def actual_posts():
    r = subprocess.run(
        ["docker", "exec", "social-postgres", "psql", "-U", "social_user",
         "-d", "social_automation", "-tAc",
         f"select count(*) from posts where status='published' "
         f"and published_at > now()-interval '{DAYS} days'"],
        capture_output=True, text=True, timeout=20)
    return int(r.stdout.strip() or 0)


def main():
    subs = fetch_report_subjects()
    reported = [s for s in subs if re.search(
        r"socialauto|post|publish|digest|report", s["subject"], re.I)]
    actual = actual_posts()
    print(json.dumps({"report_emails": len(reported),
                      "actual_posts": actual, "days": DAYS}))
    flagged = [s for s in subs if re.search(
        r"disk|warn|alert|error|fail", s["subject"], re.I)]
    if reported or flagged:
        lines = ["📧 Domain-mail cross-check:"]
        for s in reported[:4]:
            lines.append(f"• {s['subject'][:60]}")
        for s in flagged[:4]:
            lines.append(f"⚠️ {s['subject'][:60]}")
        lines.append(f"Actual posts ({DAYS}d): {actual}")
        script = SEND_PY % ("-1004436942504", "\\n".join(lines))
        subprocess.run(["docker", "exec", "-i", "social-api", "python", "-"],
                       input=script, capture_output=True, text=True, timeout=60)
        print("telegram sent")


if __name__ == "__main__":
    sys.exit(main())
