#!/usr/bin/env python3
"""synigoros_status.py — poll the Ombudsman tracking page for a complaint's status.

    synigoros_status.py [--mail EMAIL] [--kpa KPA] [--bridge URL] [--notify]

Fill the tracking form on the adhoc browser bridge, click Ταυτοποίηση,
extract Κατάσταση + Αριθμός Πρωτοκόλλου. Exit 0 with the status line, 1 on
identification failure / bridge error / empty status. Designed to run
inside social-api (BRIDGE=http://browser-novnc-adhoc:9224) or anywhere
the bridge is reachable.

Credentials come from SYNIGOROS_MAIL + SYNIGOROS_KPA (or the --mail/--kpa
flags) — nothing identifying is committed.

--notify posts to the Cloudless Telegram channel when the extracted status
differs from the last recorded run (state in /tmp/synigoros_last.json).
"""

import base64
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BRIDGE = "http://browser-novnc-adhoc:9224"
TRACK = "https://www.synigoros.gr/el/anafora/parakolouthisi-anaforas"
MAIL = os.environ.get("SYNIGOROS_MAIL", "")
KPA = os.environ.get("SYNIGOROS_KPA", "")


def call(path, data, tries=3):
    for i in range(tries):
        try:
            r = urllib.request.Request(
                f"{BRIDGE}{path}",
                data=json.dumps(data).encode(),
                headers={"Content-Type": "application/json"},
            )
            return json.loads(urllib.request.urlopen(r, timeout=40).read())
        except urllib.error.HTTPError as e:
            body = e.read()[:150]
            if (
                e.code == 409 or (e.code == 500 and b"context was destroyed" in body)
            ) and i < tries - 1:
                time.sleep(15)
                continue
            raise SystemExit(f"bridge {e.code}: {body}")


def ev(expr):
    return call("/session/evaluate", {"expression": expr}).get("result")


# ── OTP lane: Synigoros emails a 6-digit code after identify ─────────
# Ported from scripts/synigoros-monitor/monitor.js — a cold session can
# never reach the status page without submitting it. The code is fetched
# from Gmail (gmail-token.json refresh_token, readonly scope).

OTP_DETECT_JS = """(()=>{if(document.getElementById('otpCode'))return true;
const ins=[...document.querySelectorAll('input')].filter(i=>
  i.offsetParent!==null && i.id!=='webMail' && i.id!=='webCode' && !i.disabled &&
  i.type!=='hidden');return ins.length>0})()"""

# tag the code input so /session/fill can target it natively
OTP_TAG_JS = """(()=>{let i=document.getElementById('otpCode');
if(!i){const ins=[...document.querySelectorAll('input')].filter(x=>
  x.offsetParent!==null && x.id!=='webMail' && x.id!=='webCode' &&
  !x.disabled && x.type!=='hidden');i=ins[0];}
if(!i)return false;i.setAttribute('data-otp-slot','1');return true})()"""

# last-resort submit when no recognizable button exists
OTP_CLICK_JS = """(()=>{const b=[...document.querySelectorAll(
  'button,input[type=submit]')].find(x=>x.offsetParent!==null);
if(b){b.click();return 'btn';}
const i=document.querySelector('[data-otp-slot]');const f=i&&i.closest('form');
if(f){if(f.requestSubmit)f.requestSubmit();else f.submit();return 'form';}
return 'none'})()"""


def _b64url(s):
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s).decode("utf-8", "replace")


def _gmail_body(payload):
    html = ""

    def walk(p):
        nonlocal html
        if not p:
            return ""
        data = (p.get("body") or {}).get("data")
        if p.get("mimeType") == "text/plain" and data:
            return _b64url(data)
        for c in p.get("parts") or []:
            t = walk(c)
            if t:
                return t
        if p.get("mimeType") == "text/html" and data and not html:
            html = _b64url(data)
        return ""

    plain = walk(payload)
    if plain:
        return plain
    return re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ") if html else ""


def _secret_dirs():
    dirs = []
    if os.environ.get("SYNIGOROS_GMAIL_SECRETS"):
        dirs.append(os.environ["SYNIGOROS_GMAIL_SECRETS"])
    dirs.append(os.path.expanduser("~/.config/devin/secrets"))
    if os.environ.get("APPDATA"):
        dirs.append(os.path.join(os.environ["APPDATA"], "devin", "secrets"))
    dirs += glob.glob("/mnt/c/Users/*/AppData/Roaming/devin/secrets")
    return [d for d in dict.fromkeys(dirs) if os.path.isdir(d)]


def _gmail_client(secret_dir):
    keys = os.path.join(secret_dir, "gcp-oauth.keys.json")
    if os.path.exists(keys):
        try:
            c = json.load(open(keys)).get("installed") or {}
            if c.get("client_id"):
                return c["client_id"], c.get("client_secret", "")
        except Exception:
            pass
    env_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", ".env"
    )
    try:
        env = open(env_path, encoding="utf-8", errors="replace").read()
        pick = lambda k: (
            (re.search(rf"^{k}=(.+)$", env, re.MULTILINE) or [None, ""])[1]
            .strip()
            .strip("\"'")
        )
        return pick("GMAIL_OAUTH_CLIENT_ID"), pick("GMAIL_OAUTH_CLIENT_SECRET")
    except OSError:
        return "", ""


def _gmail_token():
    for d in _secret_dirs():
        tp = os.path.join(d, "gmail-token.json")
        if not os.path.exists(tp):
            continue
        try:
            tok = json.load(open(tp))
        except Exception:
            continue
        cid, csec = _gmail_client(d)
        if not cid or not tok.get("refresh_token"):
            continue
        body = urllib.parse.urlencode(
            {
                "client_id": cid,
                "client_secret": csec,
                "refresh_token": tok["refresh_token"],
                "grant_type": "refresh_token",
            }
        ).encode()
        try:
            r = json.loads(
                urllib.request.urlopen(
                    urllib.request.Request(
                        "https://oauth2.googleapis.com/token",
                        data=body,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    ),
                    timeout=20,
                ).read()
            )
            if r.get("access_token"):
                return r["access_token"]
        except Exception:
            continue
    return None


def _gmail_get(at, path):
    req = urllib.request.Request(
        "https://gmail.googleapis.com" + path, headers={"Authorization": "Bearer " + at}
    )
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


_SUBJ_B64 = re.compile(r"=\?UTF-8\?B\?([^?]+)\?=", re.IGNORECASE)


def fetch_otp(since_ms, not_before_ms=0, timeout_s=90):
    """Newest 6-digit Synigoros OTP younger than ~4.5 min, or None."""
    deadline = time.time() + timeout_s
    otp_subj = re.compile(r"Κωδικός|Επιβεβαίωσης|OTP", re.IGNORECASE)
    otp_body = re.compile(r"κωδικός|επιβεβαίωσης", re.IGNORECASE)
    while time.time() < deadline:
        at = _gmail_token()
        if not at:
            return None
        try:
            lst = _gmail_get(
                at,
                "/gmail/v1/users/me/messages?q="
                + urllib.parse.quote("from:synigoros.gr newer_than:1h")
                + "&maxResults=5",
            )
        except Exception:
            lst = {}
        for m in lst.get("messages") or []:
            try:
                d = _gmail_get(
                    at, "/gmail/v1/users/me/messages/%s?format=full" % m["id"]
                )
            except Exception:
                continue
            if d.get("internalDate"):
                floor = max(
                    not_before_ms,
                    min(since_ms - 10000, int(time.time() * 1000) - 270000),
                )
                if int(d["internalDate"]) < floor:
                    continue
            headers = (d.get("payload") or {}).get("headers") or []
            subj = next(
                (h.get("value", "") for h in headers if h.get("name") == "Subject"), ""
            )
            subj = _SUBJ_B64.sub(
                lambda mm: base64.b64decode(mm.group(1)).decode("utf-8", "replace"),
                subj,
            )
            body = _gmail_body(d.get("payload"))
            if not (otp_subj.search(subj) or otp_body.search(body)):
                continue
            code = (
                re.search(
                    r"(?:κωδικός|είναι)[^\d]{0,120}\b(\d{6})\b", body, re.IGNORECASE
                )
                or re.search(r"^\s*(\d{6})\s*$", body, re.MULTILINE)
                or re.search(r"\b(\d{6})\b", body)
            )
            if code:
                return code.group(1)
        time.sleep(5)
    return None


def main() -> int:
    global BRIDGE, MAIL, KPA
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--bridge":
            BRIDGE = args[i + 1]
        if a == "--mail":
            MAIL = args[i + 1]
        if a == "--kpa":
            KPA = args[i + 1]
    if not MAIL or not KPA:
        raise SystemExit("need SYNIGOROS_MAIL + SYNIGOROS_KPA (env or --mail/--kpa)")

    # any platform arg just boots the browser — navigate handles the real URL.
    # unforced: a forced start kills a live publishing/login session.
    start = call("/session/start", {"platform": "reddit", "interactive": False})
    owns = not start.get("reused")  # a reused session belongs to another caller
    try:
        time.sleep(4)
        call("/session/navigate", {"url": TRACK})
        for _ in range(10):  # wait for the tracking form
            if ev("!!document.getElementById('webMail')") or ev(
                "document.body.innerText.includes('Κατάσταση')"
            ):
                break
            time.sleep(3)
        else:
            raise SystemExit("tracking page never rendered")
        if ev("!!document.getElementById('webMail')"):  # fresh session → identify
            ev(
                "(()=>{const m=document.getElementById('webMail');m.value=%s;"
                "m.dispatchEvent(new Event('input',{bubbles:true}));"
                "const c=document.getElementById('webCode');c.value=%s;"
                "c.dispatchEvent(new Event('input',{bubbles:true}));return 1})()"
                % (json.dumps(MAIL), json.dumps(KPA))
            )
            call("/session/click", {"selector": "button"})
            # bounded poll: wait for the status view or the explicit
            # failure text. Synigoros gates identify behind an emailed
            # 6-digit OTP — on the prompt, fetch the code from Gmail and
            # feed it back, up to 3 attempts ~20s apart.
            failed = False
            identify_at = time.time()
            last_submit = 0.0
            otp_tries = 0
            last_otp_at = 0.0
            deadline = identify_at + 240
            while time.time() < deadline:
                time.sleep(3)
                if ev("document.body.innerText.includes('Κατάσταση')"):
                    break
                if ev("document.body.innerText.includes('Δεν έγινε ταυτοποίηση')"):
                    failed = True
                    break
                if (
                    otp_tries < 3
                    and time.time() - last_otp_at >= 20
                    and ev(OTP_DETECT_JS)
                ):
                    otp_tries += 1
                    last_otp_at = time.time()
                    print(f"OTP prompt (attempt {otp_tries}) — fetching code")
                    code = fetch_otp(int(identify_at * 1000), int(last_submit * 1000))
                    if not code:
                        print("OTP fetch failed/timed out — will retry")
                        continue
                    # native fill on the tagged input, then a real
                    # Playwright click — synthetic KeyboardEvent/Enter does
                    # not submit this form (proven 2026-11-12). The OTP
                    # screen reuses the same «Ταυτοποίηση» submit control.
                    if not ev(OTP_TAG_JS):
                        print("OTP input not found — will retry")
                        continue
                    try:
                        call("/session/fill", {"selector": "[data-otp-slot]", "value": code})
                    except SystemExit as e:
                        print(f"OTP fill failed: {e}")
                        continue
                    submitted = False
                    try:
                        call("/session/click", {"selector": "button", "text": "Ταυτοποίηση"})
                        submitted = True
                    except SystemExit:
                        pass
                    if not submitted:
                        how = ev(OTP_CLICK_JS)
                        print(f"OTP fallback submit: {how}")
                    else:
                        print("OTP acquired — submitted (Ταυτοποίηση)")
                    last_submit = time.time()
            if failed:
                print("IDENTIFY FAILED — wrong mail/ΚΠΑ pair")
                return 1
        out = ev("""(()=>{const t=document.body.innerText;
          const grab=(re)=>{const m=t.match(re);return m?m[1].trim():''};
          return JSON.stringify({
            status: grab(/Κατάσταση\\s*\\n([^\\n]+)/) || grab(/(Αναμονή[^\\n]*|Προς Αποδοχή[^\\n]*|Επεξεργασία[^\\n]*|Ολοκληρώθηκε[^\\n]*|Σε εξέλιξη[^\\n]*|Σε επεξεργασία[^\\n]*)/),
            proto:  grab(/Αριθμός Πρωτοκόλλου\\s*\\n?\\s*(\\d+)/),
            kpa:    grab(/Κωδικός Παρακολούθησης Αναφοράς\\s*\\n?\\s*(\\d+)/)
          })})()""")
        data = json.loads(out)
        # a cached session may show a DIFFERENT complaint's status — the
        # form never ran, so the displayed ΚΠΑ must match the requested one
        if data.get("kpa") and data["kpa"] != KPA:
            print(out)
            print(
                f"WRONG-COMPLAINT — page shows ΚΠΑ {data['kpa']}, requested {KPA} "
                "(cached session; reset required)"
            )
            return 1
        # an empty status means the page left the form in an unexpected
        # state (verification challenge, slow transition) — not a success
        if not data.get("status"):
            print(out)
            print("INCOMPLETE — status empty (challenge or transition); retry later")
            return 1
        print(out)
        if "--notify" in sys.argv:
            _notify(out)
        return 0
    finally:
        # release the pinned bridge session — a held session 409s every
        # other platform for five minutes. /session/stop is the real
        # endpoint (there is no /session/close); skip it when we reused
        # someone else's session.
        if owns:
            try:
                call("/session/stop", {}, tries=1)
            # BaseException: call() raises SystemExit on non-retryable
            # errors — a stop failure must never mask the poll result
            except BaseException:
                pass


STATE = "/tmp/synigoros_last.json"
CHAT = "-1004436942504"  # Cloudless — Automation HQ channel


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

        from app.core.security import decrypt_token
        from app.db.session import async_session_maker
        from app.models import SocialAccount
        from app.services.telegram_api import TelegramAPIClient
        from sqlalchemy import select

        async def _send():
            async with async_session_maker() as db:
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
                enc = acc.meta_data.get("bot_token_enc")
                tok = (
                    decrypt_token(enc.encode() if isinstance(enc, str) else enc)
                    if enc
                    else decrypt_token(acc.access_token_enc)
                )
                await TelegramAPIClient(tok).send_message(
                    CHAT, f"📋 Synigoros — status changed:\n{cur}"
                )

        asyncio.run(_send())
        print("telegram-notified")
    except Exception as e:
        print("notify-skip:", e)


if __name__ == "__main__":
    sys.exit(main())
