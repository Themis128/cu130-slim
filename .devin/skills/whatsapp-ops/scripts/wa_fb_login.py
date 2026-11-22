#!/usr/bin/env python3
"""wa_fb_login.py — heal the debug-Chrome (:9229) Facebook session.

wa_oauth_drive.py needs a logged-in FB session in the debug chrome or the
dialog/oauth URL bounces to login.php. This driver performs the full
facebook.com login unattended:

  email + password -> 2FA method picker -> "Authentication app" -> TOTP
  (META_TOTP_SECRET) -> possible "Trust this device" -> feed.

Falls back to META_BACKUP_CODES if the app option isn't offered.
Never prints passwords, codes, or secrets.

Run:  python3 wa_fb_login.py          # drive login on :9229
      python3 wa_fb_login.py --check  # report session state only
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
ENV_PATH = "/home/tbaltzakis/cu130-slim/.env"
TIMEOUT = int(os.environ.get("FB_LOGIN_TIMEOUT", "180"))


def _env(name):
    try:
        for line in open(ENV_PATH):
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return os.environ.get(name, "")


def _totp(secret):
    key = base64.b32decode(secret.replace(" ", "").upper())
    digest = hmac.new(key, struct.pack(">Q", int(time.time()) // 30),
                      hashlib.sha1).digest()
    off = digest[-1] & 0xF
    return str((struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF)
               % 1000000).zfill(6)


def tabs():
    return json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=15).read())


def find_fb_tab():
    cands = [t for t in tabs() if t.get("type") == "page"
             and ("facebook.com" in t.get("url", "")
                  or "fb.com" in t.get("url", ""))]
    # prefer a login page, else any fb tab
    for t in cands:
        if "login" in t.get("url", ""):
            return t
    return cands[0] if cands else None


def new_tab(url):
    req = urllib.request.Request(CDP + "/json/new?" + urllib.parse.quote(url, safe=""),
                                 method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


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


def fb_eval(expr, timeout=20):
    t = find_fb_tab()
    if not t:
        return {"EXC": "no fb tab"}
    try:
        return ev(t["webSocketDebuggerUrl"], expr, timeout=timeout)
    except Exception as e:
        return {"EXC": str(e)[:160]}


def fb_url():
    t = find_fb_tab()
    return (t or {}).get("url", "")


def fb_body(n=1200):
    r = fb_eval("({u:location.href,b:document.body?"
                "document.body.innerText.slice(0,%d):''})" % n)
    return r if isinstance(r, dict) and "u" in r else {"u": "", "b": str(r)}


def click_text(patterns):
    return fb_eval("""(()=>{
      const pats=%s.map(p=>new RegExp(p,'i'));
      const els=[...document.querySelectorAll(
        'button,[role=button],input[type=submit],div[role=button],a')];
      const vis=els.filter(x=>x.getClientRects().length>0);
      for(const re of pats){
        const b=vis.find(x=>re.test(
          (x.innerText||x.value||x.getAttribute('aria-label')||'').trim()));
        if(b){b.scrollIntoView({block:'center'});b.click();
          return{clicked:(b.innerText||b.value||'').trim().slice(0,60)};}
      }
      return{err:'notfound',labels:vis.map(x=>(x.innerText||x.value||'')
        .trim().slice(0,30)).filter(Boolean).slice(0,15)};
    })()""" % json.dumps(patterns))


def fill(sel_js, value):
    return fb_eval("""(()=>{
      const el=%s;
      if(!el)return{err:'noinput'};
      el.focus();
      const setter=Object.getOwnPropertyDescriptor(
        el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:
        el.tagName==='SELECT'?HTMLSelectElement.prototype:
        HTMLInputElement.prototype,'value').set;
      setter.call(el,%s);
      el.dispatchEvent(new Event('input',{bubbles:true}));
      el.dispatchEvent(new Event('change',{bubbles:true}));
      return{ok:1};
    })()""" % (sel_js, json.dumps(value)))


def classify(u, body):
    b = (body or "").lower()
    if "checkpoint" in b or "suspicious" in b or "unusual activity" in b:
        return "checkpoint"
    if "two-factor" in b or "two step" in b or "authentication app" in b \
       or "enter the code" in b or "confirmation code" in b \
       or "check your notifications" in b:
        return "2fa"
    if "trust this device" in b or "save browser" in b or "not now" in b \
       and "logged" in b:
        return "trust"
    if "email or phone" in b or "email address or phone number" in b \
       or ("password" in b and ("log in" in b or "sign in" in b)):
        return "login"
    if "what's on your mind" in b or "create a post" in b or \
       "home" == b.strip()[:4]:
        return "feed"
    if "choose a page" in b or "switch" in b and "page" in b:
        return "pagepicker"
    return "unknown"


def session_alive():
    """Logged-in probe: c_user cookie present on facebook.com.
    (fetch('/me') false-positives — logged-out /me lands on the homepage.)"""
    r = fb_eval("document.cookie.match(/c_user=(\\d+)/)?1:0")
    return r == 1


def main():
    if "--check" in sys.argv:
        st = fb_body(400)
        print("url:", fb_url()[:100])
        print("class:", classify(st.get("u", ""), st.get("b", "")))
        print("alive-probe:", session_alive())
        sys.exit(0)

    email = (_env("FACEBOOK_USERNAME") or _env("FACEBOOK_EMAIL")
             or _env("FB_EMAIL") or _env("META_EMAIL"))
    password = _env("FACEBOOK_PASSWORD") or _env("FB_PASSWORD") \
        or _env("META_PASSWORD")
    totp_secret = _env("META_TOTP_SECRET")
    backup = _env("META_BACKUP_CODES")
    if not email or not password:
        print("FATAL: no FB credentials in .env")
        sys.exit(2)

    # ensure a tab on facebook.com
    if not find_fb_tab():
        t = new_tab("https://www.facebook.com/")
        print("opened tab", t.get("id"))
        time.sleep(6)

    deadline = time.time() + TIMEOUT
    last = ""
    backup_idx = 0
    while time.time() < deadline:
        st = fb_body()
        u, body = st.get("u", ""), st.get("b", "")
        state = classify(u, body)
        if state != last:
            print("screen:", state, "|", u[:90])
            print("  body:", (body or "")[:200].replace("\n", " "))
            last = state

        if state == "feed":
            if session_alive():
                print("DONE: FB session live")
                sys.exit(0)
            # feed-looking page but probe failed — keep trying
        elif state == "login":
            f1 = fill("document.querySelector('input[name=email],input#email,input[type=email],input[name=identifier]')", email)
            time.sleep(0.5)
            f2 = fill("document.querySelector('input[name=pass],input#pass,input[type=password]')", password)
            print("  fill:", f1, f2)
            time.sleep(0.5)
            r = click_text([r"^log in$", r"^sign in$", r"log in"])
            print("  submit:", r)
            time.sleep(5)
        elif state == "2fa":
            # prefer authentication-app path
            r = click_text([r"authentication app", r"try another way",
                            r"use.*code", r"enter.*code"])
            print("  2fa nav:", r)
            time.sleep(2)
            # if a code input is present, fill TOTP
            has_code = fb_eval("""!!document.querySelector(
              'input[name=approvals_code],input[name=code',
              +"'input[autocomplete='one-time-code'],input[inputmode=numeric],"
              +"input[type=tel],input[type=text][maxlength='6']')""")
            if has_code:
                code = _totp(totp_secret) if totp_secret else None
                if not code and backup:
                    codes = [c.strip() for c in backup.replace(",", " ").split()
                             if c.strip()]
                    code = codes[min(backup_idx, len(codes) - 1)]
                    backup_idx += 1
                if code:
                    fill("document.querySelector('input[name=approvals_code],"
                         "input[name=code'],"
                         "input[autocomplete='one-time-code'],"
                         "input[inputmode=numeric],input[type=tel],"
                         "input[type=text][maxlength='6']')", code)
                    print("  code submitted")
                    time.sleep(1)
                    click_text([r"^continue$", r"^confirm$", r"^next$",
                                r"submit"])
                    time.sleep(5)
            else:
                # method picker — pick authenticator app
                r2 = click_text([r"authentication app", r"authenticator",
                                 r"totp", r"backup code"])
                print("  method pick:", r2)
                time.sleep(3)
        elif state == "trust":
            r = click_text([r"not now", r"^close$", r"^ok$", r"got it"])
            print("  trust dismiss:", r)
            time.sleep(3)
        elif state == "checkpoint":
            print("FAIL: checkpoint/suspicious-activity wall — needs human review")
            sys.exit(3)
        elif state == "pagepicker":
            r = click_text([r"cloudless", r"continue", r"switch"])
            print("  pagepick:", r)
            time.sleep(3)
        else:
            # unknown — try generic continue once
            r = click_text([r"continue", r"next", r"allow", r"got it"])
            if r and not r.get("err"):
                print("  generic click:", r)
                time.sleep(3)

        time.sleep(3)

    print("FAIL: timeout; last url:", fb_url()[:120])
    sys.exit(4)


if __name__ == "__main__":
    main()
