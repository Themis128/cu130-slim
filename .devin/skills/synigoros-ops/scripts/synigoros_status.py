#!/usr/bin/env python3
"""synigoros_status.py — poll the Ombudsman tracking page for a complaint's status.

    synigoros_status.py [--mail EMAIL] [--kpa KPA] [--bridge URL]

Fill the tracking form on the adhoc browser bridge, click Ταυτοποίηση,
extract Κατάσταση + Αριθμός Πρωτοκόλλου. Exit 0 with the status line, 1 on
identification failure / bridge error. Designed to run inside social-api
(BRIDGE=http://browser-novnc-adhoc:9224) or anywhere the bridge is reachable.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

BRIDGE = "http://browser-novnc-adhoc:9224"
TRACK = "https://www.synigoros.gr/el/anafora/parakolouthisi-anaforas"
MAIL  = os.environ.get("SYNIGOROS_MAIL", "")
KPA   = os.environ.get("SYNIGOROS_KPA", "")

def call(path, data, tries=3):
    for i in range(tries):
        try:
            r = urllib.request.Request(f"{BRIDGE}{path}",
                data=json.dumps(data).encode(),
                headers={"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(r, timeout=40).read())
        except urllib.error.HTTPError as e:
            body = e.read()[:150]
            if (e.code == 409 or (e.code == 500 and b"context was destroyed" in body)) and i < tries - 1:
                time.sleep(15)
                continue
            raise SystemExit(f"bridge {e.code}: {body}")

def ev(expr):
    return call("/session/evaluate", {"expression": expr}).get("result")

def main() -> int:
    global BRIDGE, MAIL, KPA
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--bridge":
            BRIDGE = args[i+1]
        if a == "--mail":
            MAIL   = args[i+1]
        if a == "--kpa":
            KPA    = args[i+1]
    if not MAIL or not KPA:
        raise SystemExit("need SYNIGOROS_MAIL + SYNIGOROS_KPA (env or --mail/--kpa)")

    # any platform arg just boots the browser — navigate handles the real URL.
    # close first: another task may hold the shared session (409).
    try:
        call("/session/close", {})
    except SystemExit:
        pass
    call("/session/start", {"platform": "reddit", "interactive": False, "force": True})
    time.sleep(4)
    call("/session/navigate", {"url": TRACK})
    for _ in range(10):                      # wait for the tracking form
        if ev("!!document.getElementById('webMail')") or \
           ev("document.body.innerText.includes('Κατάσταση')"):
            break
        time.sleep(3)
    else:
        raise SystemExit("tracking page never rendered")
    if ev("!!document.getElementById('webMail')"):   # fresh session → identify
        ev("(()=>{const m=document.getElementById('webMail');m.value=%s;"
           "m.dispatchEvent(new Event('input',{bubbles:true}));"
           "const c=document.getElementById('webCode');c.value=%s;"
           "c.dispatchEvent(new Event('input',{bubbles:true}));return 1})()"
           % (json.dumps(MAIL), json.dumps(KPA)))
        call("/session/click", {"selector": "button"})
        time.sleep(14)
        if ev("document.body.innerText.includes('Δεν έγινε ταυτοποίηση')"):
            print("IDENTIFY FAILED — wrong mail/ΚΠΑ pair")
            return 1
    out = ev("""(()=>{const t=document.body.innerText;
      const grab=(re)=>{const m=t.match(re)
return m?m[1].trim():''}
      return JSON.stringify({
        status: grab(/Κατάσταση\\s*\\n([^\\n]+)/) || grab(/(Αναμονή[^\\n]*|Επεξεργασία[^\\n]*|Ολοκληρώθηκε[^\\n]*)/),
        proto:  grab(/Αριθμός Πρωτοκόλλου\\s*\\n?\\s*(\\d+)/),
        kpa:    grab(/Κωδικός Παρακολούθησης Αναφοράς\\s*\\n?\\s*(\\d+)/)
      })})()""")
    data = json.loads(out)
    # a cached session may show a DIFFERENT complaint's status — the
    # form never ran, so the displayed ΚΠΑ must match the requested one
    if data.get("kpa") and data["kpa"] != KPA:
        print(out)
        print(f"WRONG-COMPLAINT — page shows ΚΠΑ {data['kpa']}, requested {KPA} "
              "(cached session; reset required)")
        _release()
        return 1
    # empty status = challenge/transition state, not a successful poll
    if not data.get("status"):
        print(out)
        print("INCOMPLETE — status empty (challenge or transition); retry later")
        _release()
        return 1
    print(out)
    if "--notify" in sys.argv:
        _notify(out)
    _release()
    return 0

def _release() -> None:
    """Release the pinned bridge session — a held session 409s other platforms."""
    try:
        call("/session/close", {})
    except Exception:
        pass

STATE = "/tmp/synigoros_last.json"
CHAT  = "-1004436942504"                 # Cloudless — Automation HQ channel

def _notify(payload: str) -> None:
    """Alert on status change via the telegram bot — skipped if unchanged."""
    try:
        cur = json.loads(payload).get("status", "")
        last = None
        if os.path.exists(STATE):
            with open(STATE) as _f:
                last = json.load(_f).get("status")
        with open(STATE, "w") as _f:
            json.dump({"status": cur}, _f)
        if not cur or cur == last:
            return
        import asyncio
        from app.db.session import async_session_maker
        from sqlalchemy import select
        from app.models import SocialAccount
        from app.services.telegram_api import TelegramAPIClient
        from app.core.security import decrypt_token
        async def _send():
            async with async_session_maker() as db:
                acc = (await db.execute(select(SocialAccount).where(
                    SocialAccount.platform == "telegram",
                    SocialAccount.status == "active").limit(1))).scalar_one()
                enc = acc.meta_data.get("bot_token_enc")
                tok = decrypt_token(enc.encode() if isinstance(enc, str) else enc) if enc \
                    else decrypt_token(acc.access_token_enc)
                await TelegramAPIClient(tok).send_message(
                    CHAT, f"📋 Synigoros — status changed:\n{cur}")
        asyncio.run(_send())
        print("telegram-notified")
    except Exception as e:
        print("notify-skip:", e)

import os  # noqa: E402  (kept at end — script-style module)

if __name__ == "__main__":
    sys.exit(main())
