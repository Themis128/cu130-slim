#!/usr/bin/env python3
"""wa_oauth_drive.py — WhatsApp Cloud API token re-auth driver.

The whatsapp social_accounts row + WHATSAPP_ACCESS_TOKEN die with every
Meta password change (OAuthException 190/460). The WABA, phone number
(+306977777838) and webhook subscription survive — only the user token
needs renewing. `/api/v1/auth/oauth/whatsapp/authorize` mints a Facebook
Login URL carrying whatsapp_business_management/messaging scopes; this
driver completes it through the debug Chrome on CDP :9229 (logged-in FB
session), handles the consent + business-asset picker screens, and the
server-side callback stores the fresh long-lived token encrypted in the
row's meta_data.

Screens handled (verified patterns from meta-oauth-ops):
  - "Continue as <name>" / "Continue" consent
  - permission review "Got it" / "Continue"
  - WABA/business asset picker: "Select all" checkbox or the business row,
    then Continue/Next
  - final "Allow" / "Continue" -> 302 to oauth/facebook/callback

Run:  python3 wa_oauth_drive.py           # full drive
      python3 wa_oauth_drive.py --verify # probe stored token only
Never prints tokens, codes, or passwords.
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

try:
    WSL_HOST_IP = os.environ.get("WSL_HOST_IP") or subprocess.run(
        ["sh", "-c", "ip route | awk '/default/ {print $3; exit}'"],
        capture_output=True, text=True, timeout=5).stdout.strip() \
        or "192.168.96.1"
except Exception:
    WSL_HOST_IP = "192.168.96.1"

CDP = "http://" + WSL_HOST_IP + ":9229"
TIMEOUT = int(os.environ.get("WA_DRIVE_TIMEOUT", "240"))


def _container():
    for c in ("social-api", "social-api-green"):
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", c],
                           capture_output=True, text=True)
        if r.stdout.strip() == "true":
            return c
    return "social-api"


def api_py(script):
    r = subprocess.run(["docker", "exec", "-i", _container(), "python3", "-"],
                       input=script, capture_output=True, text=True, timeout=120)
    return (r.stdout or "") + (r.stderr or "")


# ---------- CDP plumbing (same mechanics as ig2_oauth_drive) ----------

def tabs():
    return json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=15).read())


def new_tab(url):
    req = urllib.request.Request(CDP + "/json/new?" + urllib.parse.quote(url, safe=""),
                                 method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def find_tab(substr, only_id=None):
    for t in tabs():
        if only_id and t.get("id") != only_id:
            continue
        if t.get("type") == "page" and substr in t.get("url", ""):
            return t
    return None


def ev(ws_url, expr, timeout=20):
    from websockets.sync.client import connect
    with connect(ws_url, max_size=None) as ws:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
                "expression": expr, "returnByValue": True,
                "awaitPromise": True}}))
        end = time.time() + timeout
        while time.time() < end:
            m = json.loads(ws.recv(timeout=timeout))
            if m.get("id") == 1:
                r = m.get("result", {})
                if "exceptionDetails" in r:
                    return {"EXC": str(r["exceptionDetails"])[:200]}
                return r.get("result", {}).get("value")
    return None


def drive_tab(substr, expr, only_id=None):
    t = find_tab(substr, only_id=only_id)
    if not t:
        return {"EXC": "no tab: " + substr}
    try:
        return ev(t["webSocketDebuggerUrl"], expr)
    except Exception as e:
        return {"EXC": str(e)[:160]}


def page_state(substr="facebook.com", only_id=None):
    r = drive_tab(substr, "({u:location.href,b:document.body?"
                          "document.body.innerText.slice(0,900):''})",
                  only_id=only_id)
    return r if isinstance(r, dict) and "u" in r else {"u": "", "b": str(r)}


def click_first(substr, patterns, only_id=None):
    """Click the first visible button/role=button matching any regex.
    Returns clicked label or {err}."""
    pats = json.dumps(patterns)
    return drive_tab(substr, """(()=>{
      const pats=%s.map(p=>new RegExp(p,'i'));
      const bs=[...document.querySelectorAll(
        'button,[role=button],input[type=submit],div[role=button],a[role=button]')];
      const vis=bs.filter(x=>x.getClientRects().length>0);
      for(const re of pats){
        const b=vis.find(x=>re.test((x.innerText||x.value||x.getAttribute('aria-label')||'').trim()));
        if(b){b.scrollIntoView({block:'center'});b.click();
          return{clicked:(b.innerText||b.value||'').trim().slice(0,60)};}
      }
      return{err:'notfound',labels:vis.map(x=>(x.innerText||x.value||'').trim().slice(0,30)).filter(Boolean).slice(0,15)};
    })()""" % pats, only_id=only_id)


def click_checkbox_near(substr, needle_re, only_id=None):
    """Tick a checkbox/radio whose row text matches needle_re (asset pickers)."""
    return drive_tab(substr, """(()=>{
      const re=new RegExp(%s,'i');
      const cbs=[...document.querySelectorAll('input[type=checkbox],input[type=radio],[role=checkbox]')];
      for(const cb of cbs){
        let row=cb.closest('div[role=row],[role=option],li,label,div');
        let txt=(row?row.innerText:'')+ ' ' + (cb.getAttribute('aria-label')||'');
        if(re.test(txt)&&!cb.checked&&cb.getAttribute('aria-checked')!=='true'){
          cb.scrollIntoView({block:'center'});cb.click();
          return{clicked:txt.trim().slice(0,80)};}
      }
      return{err:'nocb'};
    })()""" % json.dumps(needle_re), only_id=only_id)


# ---------- mint + verify ----------

def mint_url():
    out = api_py("""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.config import settings
from app.core.security import sign_oauth_state
from urllib.parse import quote

async def main():
    eng = create_async_engine(settings.DATABASE_URL)
    async with eng.connect() as c:
        tid = await c.scalar(text(
            "SELECT team_id FROM social_accounts WHERE platform='whatsapp' "
            "ORDER BY updated_at DESC LIMIT 1"))
    scope = ("whatsapp_business_management,whatsapp_business_messaging,"
             "business_management,pages_show_list")
    print("CID", bool(settings.FACEBOOK_CLIENT_ID))
    print("URL",
          "https://www.facebook.com/dialog/oauth"
          f"?client_id={settings.FACEBOOK_CLIENT_ID}"
          f"&redirect_uri={quote(settings.FACEBOOK_REDIRECT_URI, safe='')}"
          f"&response_type=code&scope={scope}"
          f"&state={sign_oauth_state({'t': str(tid), 'p': 'whatsapp'})}"
          f"&display=page")

asyncio.run(main())
""")
    url = next((l[4:] for l in out.splitlines() if l.startswith("URL ")), "")
    return "CID True" in out, url


def verify_stored():
    out = api_py("""
import asyncio, json, urllib.request, urllib.parse
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.config import settings
from app.core.security import decrypt_token

async def main():
    eng = create_async_engine(settings.DATABASE_URL)
    async with eng.connect() as c:
        r = await c.execute(text(
            "SELECT access_token_enc, meta_data, status FROM social_accounts "
            "WHERE platform='whatsapp' ORDER BY updated_at DESC LIMIT 1"))
        enc, meta, status = r.fetchone()
    meta = meta or {}
    tok = decrypt_token(enc) if enc else ""
    pnid = meta.get("phone_number_id") or ""
    waba = meta.get("waba_id") or ""
    print("STATUS", status, "| PNID", bool(pnid), "| WABA", bool(waba))
    if not tok:
        print("RESULT dead:no-token"); return
    # probe the WABA phone_numbers edge — proves whatsapp scopes live
    u = ("https://graph.facebook.com/v21.0/"
         + (waba or pnid) + "/phone_numbers?access_token="
         + urllib.parse.quote(tok))
    try:
        d = json.load(urllib.request.urlopen(u, timeout=20))
        n = len(d.get("data", []))
        print("RESULT live phones=%d" % n)
    except urllib.error.HTTPError as e:
        body = e.read()[:160].decode("utf-8", "replace")
        print("RESULT dead http=%d %s" % (e.code, body[:120]))
    except Exception as e:
        print("RESULT err", str(e)[:120])

asyncio.run(main())
""")
    print(out.strip())
    return "RESULT live" in out


# ---------- drive ----------

def classify(body):
    b = (body or "").lower()
    if "log in to facebook" in b or "log into facebook" in b or \
       "email or phone" in b and "password" in b:
        return "login_wall"
    if "select whatsapp" in b or "choose whatsapp" in b or \
       ("whatsapp business account" in b and "select" in b):
        return "waba_picker"
    if "select a business" in b or "choose a business" in b or \
       ("business" in b and ("asset" in b or "portfolio" in b) and "select" in b):
        return "biz_picker"
    if "review" in b and ("permission" in b or "access" in b):
        return "review"
    if "continue as" in b:
        return "continue_as"
    if "allow" in b and ("permission" in b or "access" in b or "continue" in b):
        return "allow"
    return "unknown"


def main():
    if "--verify" in sys.argv:
        sys.exit(0 if verify_stored() else 1)

    cid_ok, url = mint_url()
    if not cid_ok or not url:
        print("FATAL: could not mint authorize URL")
        sys.exit(2)
    print("minted authorize URL (whatsapp scopes)")

    t = new_tab(url)
    our_id = t.get("id")
    print("opened tab", our_id)

    deadline = time.time() + TIMEOUT
    last_click = ""
    last_state = ""
    idle = 0
    while time.time() < deadline:
        # resolve OUR tab by id (it navigates; url substring goes stale)
        our = next((x for x in tabs() if x.get("id") == our_id), None)
        our_url = (our or {}).get("url", "")
        if "oauth/facebook/callback" in our_url:
            print("callback reached:", our_url[:110])
            if "error=" in our_url:
                print("FAIL: consent denied server-side:", our_url[:200])
                sys.exit(3)
            print("waiting for token write…")
            time.sleep(8)
            if verify_stored():
                print("DONE: whatsapp token live")
                sys.exit(0)
            print("callback seen but token not live yet — retrying verify")
            time.sleep(10)
            if verify_stored():
                print("DONE: whatsapp token live")
                sys.exit(0)
            print("FAIL: callback hit but stored token still dead")
            sys.exit(4)

        st = page_state("", only_id=our_id)
        u = our_url or st.get("u", "")
        body = st.get("b", "")
        state = classify(body)
        if state != last_state:
            print("screen:", state, "|", u[:80])
            print("  body:", (body or "")[:160].replace("\n", " "))
            last_state = state
            idle = 0
        else:
            idle += 1

        if state == "login_wall":
            print("FAIL: FB login wall — sidecar session needed first")
            sys.exit(3)

        clicked = None
        if state == "waba_picker":
            # prefer ticking our business/WABA row then Continue
            r = click_checkbox_near("", r"cloudless|whatsapp|1558125105019725", only_id=our_id)
            print("  picker tick:", r)
            time.sleep(1)
            clicked = click_first("", [r"^continue$", r"^next$", r"^done$", r"^confirm$"], only_id=our_id)
        elif state == "biz_picker":
            r = click_checkbox_near("facebook.com", r"cloudless|1558125105019725")
            print("  biz tick:", r)
            time.sleep(1)
            clicked = click_first("", [r"^continue$", r"^next$", r"^done$", r"^confirm$", r"select all"], only_id=our_id)
        elif state == "review":
            clicked = click_first("", [r"got it", r"^continue$", r"^next$"], only_id=our_id)
        elif state == "continue_as":
            clicked = click_first("", [r"continue as", r"^continue$", r"^next$"], only_id=our_id)
        elif state == "allow":
            clicked = click_first("", [r"^allow", r"^continue$", r"^next$", r"^done$"], only_id=our_id)
        else:
            # unknown — nudge with generic continue/next/allow once per screen
            if idle > 2 and last_click != state:
                clicked = click_first("",
                    [r"continue as", r"^continue$", r"^next$", r"^allow",
                     r"got it", r"^done$", r"confirm"], only_id=our_id)
                last_click = state

        if clicked and not clicked.get("err"):
            print("  clicked:", clicked.get("clicked"))
            time.sleep(3)
        elif clicked and clicked.get("err") == "notfound" and idle > 6:
            print("  no actionable button; labels:", clicked.get("labels"))
            idle = 0

        # page may have navigated away from facebook.com (threads/IG interstitial)
        if "facebook.com" not in u and "whatsapp" not in u.lower():
            # give navigation a moment; re-resolve next loop
            pass
        time.sleep(3)

    print("FAIL: timeout")
    sys.exit(4)


if __name__ == "__main__":
    main()
