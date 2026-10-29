#!/usr/bin/env python3
"""observability_guard — enforce zero Observability billing before Dec 1 2026.

Runs daily via cron. Two jobs:

1. DRIFT: PATCH any worker whose observability.enabled drifted back on
   (CI deploys can re-enable it — wrangler defaults it on in some flows).
   Prod-critical workers stay at 5% sampling; everything else forced off.
2. DEADLINE: from Nov 20, daily Telegram ping — "cancel the Observability
   subscription in dashboard" until ACK file exists (touch
   .devin/skills/cf-billing-ops/OBS_ACK after cancelling).

Cron: 45 8 * * * python3 .../observability_guard.py >> /tmp/obs_guard.log 2>&1
"""
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.request

ACC = "fb7dc7b69b662480cd5961a4d1913c78"
KEEP = {"cloudless2": 0.05, "mail-ingest": 0.05, "postiz-ai-proxy": 0.05}
DEADLINE = dt.date(2026, 12, 1)
WARN_FROM = dt.date(2026, 11, 20)
ACK = os.path.join(os.path.dirname(__file__), "..", "OBS_ACK")


def _tok() -> str:
    for p in ("/home/tbaltzakis/cu130-slim/.env",
              "/mnt/c/Users/tbaltzakis/cu130-slim/.env"):
        if os.path.exists(p):
            for l in open(p):
                if l.startswith("CLOUDFLARE_API_TOKEN="):
                    v = l.split("=", 1)[1].strip().strip('"')
                    if v:
                        return v
    sys.exit("no CLOUDFLARE_API_TOKEN")


def _cf(path, method="GET", form=None):
    tok = _tok()
    if form is None:
        r = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{ACC}/{path}",
            headers={"Authorization": f"Bearer {tok}"})
    else:
        boundary = "----g"
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="settings"\r\n\r\n'
                f'{json.dumps(form)}\r\n--{boundary}--\r\n').encode()
        r = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{ACC}/{path}",
            data=body, method="PATCH",
            headers={"Authorization": f"Bearer {tok}",
                     "Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.loads(urllib.request.urlopen(r, timeout=20).read())


def _telegram(text: str) -> None:
    script = """
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
        tok = decrypt_token(enc.encode() if isinstance(enc,str) else enc) \\
            if enc else decrypt_token(acc.access_token_enc)
        await TelegramAPIClient(tok).send_message(%r, %r)
asyncio.run(go())
""" % ("-1004436942504", text)
    subprocess.run(["docker", "exec", "-i", "social-api", "python", "-"],
                   input=script, capture_output=True, text=True, timeout=60)


def main() -> int:
    today = dt.date.today()
    scripts = _cf("workers/scripts").get("result") or []
    fixed = []
    for w in scripts:
        wid = w["id"]
        try:
            st = _cf(f"workers/scripts/{wid}/settings").get("result") or {}
            obs = st.get("observability") or {}
            en = bool(obs.get("enabled"))
            want = KEEP.get(wid)
            if want is None and en:
                _cf(f"workers/scripts/{wid}/settings",
                    "PATCH", {"observability": {"enabled": False}})
                fixed.append(f"{wid}: forced off")
            elif want is not None and (not en or obs.get("head_sampling_rate") != want):
                _cf(f"workers/scripts/{wid}/settings",
                    "PATCH", {"observability": {"enabled": True,
                                                "head_sampling_rate": want}})
                fixed.append(f"{wid}: set 5%")
        except Exception as e:
            fixed.append(f"{wid}: ERR {str(e)[:50]}")
    print("drift fixes:", fixed or "none")

    if today >= WARN_FROM and not os.path.exists(ACK):
        days = (DEADLINE - today).days
        _telegram(
            f"⚠️ Cloudflare Observability billing starts Dec 1 ({days}d). "
            "Cancel the Observability subscription: dashboard → Billing → "
            "Subscriptions → Observability → Cancel. Workers plan untouched. "
            "Then run observability_guard.py ack")
        print("deadline ping sent")
        # final-week escalation: drive the logged-in dashboard ourselves
        if days <= 4:
            try:
                r = subprocess.run(
                    ["python3", os.path.join(os.path.dirname(__file__),
                                             "obs_cancel.py"), "run"],
                    capture_output=True, text=True, timeout=60)
                ok = "clicked" in r.stdout
                _telegram(
                    "🤖 Observability auto-cancel: " +
                    ("cancel button clicked — verify + run ack" if ok
                     else "couldn't reach dashboard (no CDP/session) — manual cancel needed"))
                print("auto-cancel:", "clicked" if ok else "failed")
            except Exception as e:
                print("auto-cancel err:", str(e)[:80])
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "ack":
        os.makedirs(os.path.dirname(ACK), exist_ok=True)
        open(ACK, "w").write(str(dt.date.today()))
        print("ack recorded — deadline pings off")
    else:
        sys.exit(main())
