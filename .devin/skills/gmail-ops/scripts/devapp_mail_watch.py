#!/usr/bin/env python3
"""devapp_mail_watch — watch Gmail for developer-platform review mail.

Targets the LinkedIn CM-API Standard Tier review (Microsoft Vetting
Services, LinkedIn developer notifications, Qualtrics confirmations).
Dedups via a state file
sends new hits to the Automation HQ Telegram
channel by exec'ing the send inside social-api (bot token stays in the
container's encrypted store).

Cron (host):  5 9 * * *  python3 <repo>/.devin/skills/gmail-ops/scripts/devapp_mail_watch.py
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
GMAIL = os.path.join(HERE, "gmail.py")
STATE = os.path.join(HERE, "..", "state", "devapp_seen.json")
QUERY = ('from:(linkedin.com OR microsoft.com OR qualtrics.com) '
         '(vetting OR "access request" OR "Community Management" OR '
         '"developer app" OR "approved" OR "review") newer_than:3d')
CHAT = "-1004436942504"

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


def search():
    r = subprocess.run(["python3", GMAIL, "search", QUERY],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        print("search failed:", r.stderr[:200], file=sys.stderr)
        return []
    out = []
    for line in r.stdout.splitlines():
        p = [x.strip() for x in line.split("|", 2)]
        if len(p) == 3 and p[2]:
            out.append({"date": p[0], "from": p[1], "subject": p[2]})
    return out


def main():
    hits = search()
    if not hits:
        print("no hits")
        return 0
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    seen = set()
    if os.path.exists(STATE):
        with open(STATE) as _f:
            seen = set(json.load(_f))
    new = [h for h in hits
           if (h["subject"] + "|" + h["date"]) not in seen]
    if not new:
        print("no new hits")
        return 0
    # Auto-act on verification links — a confirm-address GET is safe
    acted = []
    for h in new:
        if not re.search(r"vetting|verif|confirm|access request", h["subject"], re.I):
            continue
        r = subprocess.run(["python3", GMAIL, "links",
                            'from:(microsoft.com OR linkedin.com) newer_than:1d'],
                           capture_output=True, text=True, timeout=60)
        for u in r.stdout.splitlines():
            if not re.search(r"verif|confirm|vetting|validate|activate", u, re.I):
                continue
            try:
                code = urllib.request.urlopen(u, timeout=20).status
            except Exception as e:
                code = str(e)[:60]
            acted.append(f"auto-hit {u[:70]} -> {code}")
            break
    # drop a Nextcloud approval card for hits we can't auto-act
    HERE_ = os.path.dirname(os.path.abspath(__file__))
    push = os.path.join(HERE_, "..", "..", "human-gate-ops", "scripts", "approval_push.py")
    for h in new:
        subprocess.run(["python3", push, "--title", h["subject"][:60],
                        "--link", "https://mail.google.com/",
                        "--context", f"from {h['from']}",
                        "--source", "devapp_mail_watch"],
                       capture_output=True, timeout=30)
    lines = "\\n".join(
        f"• {re.sub('[^ -~]', '', h['subject'])[:70]} — {h['from'][:40]}"
        for h in new[:6])
    if acted:
        lines += "\\n" + "\\n".join(acted[:3])
    msg = "🔎 Dev-app review mail arrived:\\n" + lines
    script = SEND_PY % (CHAT, msg)
    r = subprocess.run(
        ["docker", "exec", "-i", "social-api", "python", "-"],
        input=script, capture_output=True, text=True, timeout=60)
    print("telegram:", "sent" if r.returncode == 0 else r.stderr[:200])
    seen.update(h["subject"] + "|" + h["date"] for h in new)
    with open(STATE, "w") as _f:
        json.dump(sorted(seen), _f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
