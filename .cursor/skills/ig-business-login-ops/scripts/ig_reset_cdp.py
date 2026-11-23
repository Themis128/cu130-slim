#!/usr/bin/env python3
"""ig_reset_cdp.py — Instagram forgot-password drive on debug Chrome :9229.

Why this exists: ig_pw_reset.py (Playwright fresh context) hits
"Something went wrong" on the identify step — IG fingerprint-scores
automation browsers. The SAME flow succeeds in the user's real debug
Chrome (verified live 2026-11-23: identifier -> "Email sent" dialog).
This tool codifies that CDP lane:

  login page -> "Use another profile" -> "Forgot password?" ->
  fill identifier (cloudless.gr / email / phone) -> Continue ->
  confirm "Email sent" dialog -> OK.

After it reports EMAIL-SENT, run ig_emaillogin_session.py immediately —
the web_emaillogin link expires fast.

Usage:
  ig_reset_cdp.py                # full drive, identifier cloudless.gr
  ig_reset_cdp.py --id USERNAME  # different identifier
  ig_reset_cdp.py --check        # report challenge/reset tab state only

Env: CDP_URL (default http://192.168.96.1:9229), CDP_PY python with
websockets (default ~/.venv-igdrive/bin/python3).
Exit: 0 EMAIL-SENT / 1 already-there-or-unknown / 2 flow-failed
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlparse

CDP = os.environ.get("CDP_URL", "http://192.168.96.1:9229")
WS_PY = os.environ.get("CDP_PY",
                       os.path.expanduser("~/.venv-igdrive/bin/python3"))
IDENT = "cloudless.gr"

EVAL_PY = "/tmp/_igreset_eval.py"
NAV_PY = "/tmp/_igreset_nav.py"


def _helpers():
    open(EVAL_PY, "w").write(
        "import json,sys\nfrom websockets.sync.client import connect\n"
        "ws=connect(sys.argv[1],open_timeout=20)\n"
        "ws.send(json.dumps({'id':1,'method':'Runtime.evaluate','params':"
        "{'expression':sys.argv[2],'returnByValue':True,'awaitPromise':True}}))\n"
        "r=json.loads(ws.recv())\n"
        "print(json.dumps(r.get('result',{}).get('result',{})))\n")
    open(NAV_PY, "w").write(
        "import json,sys\nfrom websockets.sync.client import connect\n"
        "ws=connect(sys.argv[1],open_timeout=20)\n"
        "ws.send(json.dumps({'id':1,'method':'Page.navigate','params':"
        "{'url':sys.argv[2]}}))\nws.recv()\n")


def tabs():
    return json.loads(urllib.request.urlopen(CDP + "/json/list",
                                             timeout=10).read())


def _is_ig_url(url):
    host = (urlparse(url).hostname or "").lower()
    return host == "instagram.com" or host.endswith(".instagram.com")


def ig_tab():
    cands = [t for t in tabs() if t.get("type") == "page"
             and _is_ig_url(t.get("url", ""))]
    # prefer a plain instagram.com/ picker tab, else any IG tab
    for t in cands:
        u = urlparse(t["url"])
        if u.path in ("", "/") and (u.hostname or "") == "www.instagram.com":
            return t
    return cands[0] if cands else None


def nav(t, url):
    subprocess.run([WS_PY, NAV_PY, t["webSocketDebuggerUrl"], url],
                   capture_output=True, timeout=30)
    time.sleep(6)


def ev(t, js):
    out = subprocess.run([WS_PY, EVAL_PY, t["webSocketDebuggerUrl"], js],
                         capture_output=True, text=True, timeout=45)
    try:
        return json.loads(json.loads(out.stdout).get("value") or "null")
    except Exception:
        return out.stdout[-200:]


def state(t):
    v = ev(t, "JSON.stringify({url:location.href.slice(0,90),"
               "text:(document.body?document.body.innerText:'').slice(0,700)})")
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {"url": "", "text": v}
    return v or {}


def click_text(t, pattern):
    js = ("(()=>{const els=[...document.querySelectorAll("
          "'a,button,div[role=button],span')];"
          "const b=els.filter(e=>new RegExp('%s','i').test("
          "(e.innerText||'').trim())&&(e.innerText||'').trim().length<60).pop();"
          "if(!b)return 'NF';b.click();return 'OK'})()" % pattern)
    return ev(t, js)


def fill_first_text(t, value):
    js = ("(()=>{const inp=document.querySelector('input[type=text]')||"
          "document.querySelector('input:not([type=hidden])');"
          "if(!inp)return 'NOINPUT';"
          "const set=Object.getOwnPropertyDescriptor("
          "window.HTMLInputElement.prototype,'value').set;"
          "set.call(inp,%s);inp.dispatchEvent(new Event('input',"
          "{bubbles:true}));inp.dispatchEvent(new Event('change',"
          "{bubbles:true}));return 'FILLED '+inp.value})()"
          % json.dumps(value))
    return ev(t, js)


def main():
    _helpers()
    ident = IDENT
    check_only = "--check" in sys.argv
    for a in sys.argv[1:]:
        if a.startswith("--id="):
            ident = a.split("=", 1)[1]
    t = ig_tab()
    if not t:
        print("no instagram tab on", CDP); return 2
    st = state(t)
    print("tab:", t["id"][:8], st.get("url", "")[:80])
    if check_only:
        print((st.get("text") or "")[:300]); return 0
    url = st.get("url", "")
    txt = (st.get("text") or "")
    # 1) get to a logged-out surface: picker -> use another profile -> login
    if "two_step_verification" in url:
        print("tab is mid-2FA-challenge — use another IG tab or wait")
        return 1
    if "accounts/login" not in url and "password/reset" not in url:
        nav(t, "https://www.instagram.com/accounts/login/")
        st = state(t); txt = st.get("text") or ""; url = st.get("url", "")
        time.sleep(2)
    if "Use another profile" in txt or "Create new account" in txt:
        print(click_text(t, "use another profile")); time.sleep(4)
        st = state(t); txt = st.get("text") or ""
    if "Forgot password" in txt:
        print(click_text(t, "forgot password")); time.sleep(5)
        st = state(t); txt = st.get("text") or ""; url = st.get("url", "")
    if "password/reset" not in url:
        nav(t, "https://www.instagram.com/accounts/password/reset/")
        time.sleep(5)
        st = state(t); txt = st.get("text") or ""; url = st.get("url", "")
    if "Find your account" not in txt and "mobile number" not in txt.lower():
        print("reset form not reached:", txt[:200]); return 2
    print("fill:", fill_first_text(t, ident)); time.sleep(1)
    print("continue:", click_text(t, "^continue$"))
    # the "Email sent" dialog can lag several seconds behind the click
    deadline = time.time() + 25
    while time.time() < deadline:
        time.sleep(3)
        st = state(t); txt = st.get("text") or ""
        if "Email sent" in txt or "email to" in txt.lower():
            print(click_text(t, "^ok$"))
            print("EMAIL-SENT — run ig_emaillogin_session.py now")
            return 0
        if "wrong" in txt.lower() or "error" in txt.lower() \
                or "Something went wrong" in txt:
            break
    print("post-continue state:", txt[:300]); return 2


if __name__ == "__main__":
    sys.exit(main())
