#!/usr/bin/env python3
"""ig2_oauth_drive.py — Instagram Business Login (instagram2) OAuth driver.

Completes `GET /api/v1/auth/oauth/instagram2/authorize` end-to-end through
the debug Chrome on CDP :9229: mints the signed authorize URL inside the
API container, navigates a tab, handles IG login → 2FA method picker →
TOTP → consent Allow, and verifies the callback wrote an active
`login_type=business_login` row with a live graph.instagram.com token.

WHY: the business_login lane publishes via graph.instagram.com with
instagram_business_* scopes — it does NOT need a Facebook Page link, so
it bypasses Meta #10 / the missing `instagram_business_account` edge.

Prereqs:
  - INSTAGRAM2_CLIENT_ID/SECRET/REDIRECT_URI reachable by the API
    container (standalone stack: deploy/socialauto-standalone/.env —
    env_file injects on CREATE, not restart; recreate after adding).
  - META_TOTP_SECRET_IG in .env (authentication-app 2FA enrolled).
  - Debug chrome reachable at http://<wsl-host>:9229.
  - INSTAGRAM_USERNAME/PASSWORD in the API container env (login form).

Run:  python3 ig2_oauth_drive.py            # full drive
      python3 ig2_oauth_drive.py --verify   # verify-only (exit 0 if live)
Never prints tokens, passwords, or TOTP codes.
"""
import base64
import hashlib
import hmac
import json
import os
import struct
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
IG_USER = "cloudless.gr"
ENV_PATH = "/home/tbaltzakis/cu130-slim/.env"
STANDALONE_ENV = "/home/tbaltzakis/cu130-slim/deploy/socialauto-standalone/.env"


def _container():
    for c in ("social-api", "social-api-green"):
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", c],
                           capture_output=True, text=True)
        if r.stdout.strip() == "true":
            return c
    return "social-api"


def _env(name):
    for path in (STANDALONE_ENV, ENV_PATH):
        try:
            for line in open(path):
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return ""


def _totp(secret):
    key = base64.b32decode(secret.replace(" ", "").upper())
    digest = hmac.new(key, struct.pack(">Q", int(time.time()) // 30),
                      hashlib.sha1).digest()
    off = digest[-1] & 0xF
    return str((struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF)
               % 1000000).zfill(6)


# ---------- CDP plumbing ----------

def tabs():
    return json.loads(
        urllib.request.urlopen(CDP + "/json/list", timeout=15).read())


def tab(substr):
    for t in tabs():
        if t.get("type") == "page" and substr in t.get("url", ""):
            return t
    return None


def ev(ws_url, expr, timeout=20):
    """Runtime.evaluate; returns value or {'EXC':...}. Reconnect each call —
    Meta navigations drop the socket constantly."""
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


def ev_tab(substr, expr):
    """Evaluate on the first tab whose URL contains substr."""
    t = tab(substr)
    if not t:
        return {"EXC": "no tab: " + substr}
    try:
        return ev(t["webSocketDebuggerUrl"], expr)
    except Exception as e:  # navigation raced — caller retries
        return {"EXC": str(e)[:160]}


def page_state(substr):
    r = ev_tab(substr, "({u:location.href,b:document.body?"
                       "document.body.innerText.slice(0,600):''})")
    return r if isinstance(r, dict) and "u" in r else {"u": "", "b": str(r)}


def click_label(substr, pattern):
    """Click first button/role=button whose text matches regex; returns its text."""
    return ev_tab(substr, """(()=>{
      const re=new RegExp(%s,'i');
      const bs=[...document.querySelectorAll(
        'button,[role=button],input[type=submit],div[role=button]')];
      const b=bs.find(x=>re.test((x.innerText||x.value||'').trim()));
      if(!b)return{err:'notfound'};
      b.scrollIntoView({block:'center'});b.click();
      return{clicked:(b.innerText||'').trim().slice(0,40)};
    })()""" % json.dumps(pattern))


# ---------- API container ----------

def api_py(script):
    r = subprocess.run(["docker", "exec", "-i", _container(), "python3", "-"],
                       input=script, capture_output=True, text=True, timeout=120)
    return (r.stdout or "") + (r.stderr or "")


def mint_url():
    out = api_py("""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.config import settings
from app.core.security import sign_oauth_state

async def main():
    eng = create_async_engine(settings.DATABASE_URL)
    async with eng.connect() as c:
        r = await c.execute(text(
            "SELECT team_id FROM social_accounts "
            "WHERE platform='instagram' AND username='cloudless.gr' "
            "ORDER BY updated_at DESC LIMIT 1"))
        tid = r.scalar()
    scope = ("instagram_business_basic,instagram_business_content_publish,"
             "instagram_business_manage_comments,"
             "instagram_business_manage_messages,"
             "instagram_business_manage_insights")
    print("CID", bool(settings.INSTAGRAM2_CLIENT_ID))
    print("URL",
          "https://www.instagram.com/oauth/authorize"
          f"?client_id={settings.INSTAGRAM2_CLIENT_ID}"
          f"&redirect_uri={settings.INSTAGRAM2_REDIRECT_URI}"
          f"&scope={scope}&response_type=code"
          f"&state={sign_oauth_state({'t': str(tid)})}")

asyncio.run(main())
""")
    cid_ok = "CID True" in out
    url = next((l[4:] for l in out.splitlines() if l.startswith("URL ")), "")
    return cid_ok, url


def verify_stored():
    """Returns (ok, username) — the business_login row is active and its
    token answers graph.instagram.com/me."""
    out = api_py("""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.config import settings
from app.core.security import decrypt_token
import httpx

async def main():
    eng = create_async_engine(settings.DATABASE_URL)
    async with eng.connect() as c:
        r = await c.execute(text(
            "SELECT username, access_token_enc FROM social_accounts "
            "WHERE platform='instagram' "
            "AND meta_data->>'login_type'='business_login' "
            "AND status='active' ORDER BY updated_at DESC LIMIT 1"))
        row = r.fetchone()
    if not row or not row[1]:
        print("RESULT none")
        return
    tok = decrypt_token(bytes(row[1]))
    async with httpx.AsyncClient(timeout=15) as h:
        r = await h.get("https://graph.instagram.com/v21.0/me",
                        params={"fields": "id,username,account_type",
                                "access_token": tok})
        ok = r.status_code == 200
        print("RESULT", "ok" if ok else "bad",
              r.json().get("username", "?") if ok else r.status_code)

asyncio.run(main())
""")
    for l in out.splitlines():
        if l.startswith("RESULT "):
            parts = l.split()
            return parts[1] == "ok", parts[2] if len(parts) > 2 else "?"
    return False, "?"


# ---------- drive ----------

def drive_login(tab_substr):
    """IG login form → 2FA picker → TOTP. Returns step name reached."""
    user = IG_USER
    pw = _env("INSTAGRAM_PASSWORD")
    secret = _env("META_TOTP_SECRET_IG")
    if not pw or not secret:
        return "missing-creds"

    r = ev_tab(tab_substr, """(()=>{
      const ins=[...document.querySelectorAll('input')];
      const u=ins.find(x=>/username|text|email/.test(
        x.type+x.name+(x.autocomplete||'')));
      const p=ins.find(x=>x.type==='password');
      if(!u||!p)return{err:'fields'};
      const set=(el,v)=>{const s=Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,'value').set;
        s.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}));};
      set(u,%s);set(p,%s);return{ok:1};
    })()""" % (json.dumps(user), json.dumps(pw)))
    if "err" in (r or {}):
        return "fields"
    click_label(tab_substr, r"^Log in$")
    time.sleep(6)
    # May land on WhatsApp-code page — switch to Authentication app.
    st = page_state("two_step_verification")
    if "WhatsApp" in st.get("b", "") or "Check your" in st.get("b", ""):
        click_label("two_step_verification", "Try another way")
        time.sleep(3)
        ev_tab("two_step_verification", """(()=>{
          const el=[...document.querySelectorAll('label,div,span')].find(
            x=>/^\\s*Authentication app\\s*$/i.test((x.innerText||'').trim()));
          if(!el)return{err:1};
          const inp=el.tagName==='LABEL'
            ?(el.querySelector('input')
              ||document.querySelector('#'+el.htmlFor))
            :(el.closest('label')?.querySelector('input')
              ||el.parentElement.querySelector('input[type=radio]'));
          (inp||el).click();return{ok:1};
        })()""")
        # Picker's own Continue is the LAST visible Continue on the page.
        ev_tab("two_step_verification", """(()=>{
          const bs=[...document.querySelectorAll('button,[role=button]')]
            .filter(x=>/^Continue$/i.test((x.innerText||'').trim()))
            .filter(b=>{const r=b.getBoundingClientRect();
              return r.y>0&&r.y<innerHeight});
          const b=bs[bs.length-1];if(!b)return{err:1};b.click();
          return{ok:1};
        })()""")
        time.sleep(4)
    # TOTP: fill + click the Continue closest BELOW the code input
    # (there are two Continues — the code submit and the method picker's).
    code = _totp(secret)
    ev_tab("two_step_verification", """(()=>{
      const inp=[...document.querySelectorAll('input')].find(
        x=>x.type==='text'||x.type==='tel');
      if(!inp)return{err:'noinput'};
      const s=Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,'value').set;
      s.call(inp,%s);inp.dispatchEvent(new Event('input',{bubbles:true}));
      const y=inp.getBoundingClientRect().y;
      const bs=[...document.querySelectorAll('button,[role=button]')]
        .filter(x=>/Continue|Confirm/i.test(x.innerText||''))
        .filter(b=>b.getBoundingClientRect().y>y);
      bs.sort((a,b)=>a.getBoundingClientRect().y-b.getBoundingClientRect().y);
      const b=bs[0];if(!b)return{err:'nobtn'};
      b.scrollIntoView({block:'center'});b.click();return{submitted:1};
    })()""" % json.dumps(code))
    time.sleep(6)
    st = page_state("two_step_verification")
    if "authentication app" in st.get("b", "").lower():
        # Stale/wrong code or missed click — one retry with a fresh code.
        return "totp-retry"
    return "2fa-done"


def main():
    verify_only = "--verify" in sys.argv

    ok, who = verify_stored()
    if ok:
        print(f"already live: business_login token answers as @{who}")
        return 0
    if verify_only:
        print("not live:", who)
        return 2

    # Fail fast: every CDP eval below needs websockets; without it ev()
    # raises inside try/except-pass wrappers and the drive polls a tab it
    # never navigated for the full 240s deadline (burned a whole run
    # 2026-11-22 on bare python3).
    try:
        import websockets.sync.client  # noqa: F401
    except ImportError:
        print("websockets missing for this interpreter — CDP drive would "
              "silently no-op. Use an interpreter that has it, e.g.: "
              "python3 -m venv ~/.venv-igdrive && "
              "~/.venv-igdrive/bin/pip install websockets && "
              "~/.venv-igdrive/bin/python ig2_oauth_drive.py")
        return 4

    cid_ok, url = mint_url()
    if not cid_ok or not url:
        print("INSTAGRAM2_* not in API container env — add to standalone "
              ".env and `docker compose up -d social-api`")
        return 2

    # Reuse the MAIN instagram tab (exact home URL — a substring match can
    # grab a stale login/reset/error tab and drive the wrong page).
    t = next((x for x in tabs() if x.get("type") == "page"
              and x.get("url", "").rstrip("/")
              == "https://www.instagram.com"), None) or tab("instagram.com")
    flow_id = None
    if t:
        flow_id = t["id"]
        try:
            ev(t["webSocketDebuggerUrl"],
               "location.href=%s" % json.dumps(url))
        except Exception:
            pass
    else:
        req = urllib.request.Request(
            CDP + "/json/new?" + urllib.parse.quote(url, safe=""),
            method="PUT")
        try:
            flow_id = json.loads(
                urllib.request.urlopen(req, timeout=15).read())["id"]
        except Exception:
            pass

    def flow_tab():
        """The tab we navigated — follow it by target id so stale login
        tabs can't steal the drive."""
        if flow_id:
            for x in tabs():
                if x.get("id") == flow_id:
                    return x
        return None

    deadline = time.time() + 240
    totp_tried = 0
    while time.time() < deadline:
        t = flow_tab()
        if not t:
            time.sleep(3)
            continue
        u = t.get("url", "")
        wid = t["webSocketDebuggerUrl"]
        if "consent/" in u or "/oauth/authorize" in u:
            r = None
            try:
                r = ev(wid, """(()=>{
                  const b=[...document.querySelectorAll(
                    'button,[role=button]')].find(
                    x=>/^Allow$|^Authorize$/i.test(
                      (x.innerText||'').trim()));
                  if(!b)return{err:1};b.click();
                  return{clicked:1};
                })()""")
            except Exception:
                pass
            if (r or {}).get("clicked"):
                print("consent: allowed")
                time.sleep(8)
                ok, who = verify_stored()
                if ok:
                    print(f"DONE: @{who} business_login token live")
                    return 0
        elif "two_step_verification" in u or "accounts/login" in u:
            if totp_tried < 3:
                phase = drive_login(u)
                totp_tried += 1
                print("login:", phase)
            time.sleep(3)
        elif "/oauth/oidc/" in u or "facebook.com" in u:
            time.sleep(3)  # in-flight redirect — wait it out
        else:
            # Account-chooser ("Log into Instagram" + profile names) has no
            # username input — click the cloudless.gr profile tile.
            try:
                r = ev(wid, """(()=>{
                  const el=[...document.querySelectorAll(
                    'div[role=button],a,button')].find(
                    x=>(x.innerText||'').trim()==='cloudless.gr');
                  if(el){el.click();return{clicked:1}}
                  return{body:document.body?
                    document.body.innerText.slice(0,150):''};
                })()""")
                if (r or {}).get("clicked"):
                    print("chooser: cloudless.gr")
            except Exception:
                pass
            time.sleep(3)
        time.sleep(2)

    ok, who = verify_stored()
    print("verify:", ok, who)
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
