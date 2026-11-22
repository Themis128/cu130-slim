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


def _host_is(url: str, domain: str) -> bool:
    """Hostname-suffix match — 'facebook.com' in url is spoofable
    (evil.com/?q=facebook.com); CodeQL py/incomplete-url-substring-sanitization."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def tabs():
    return json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=15).read())


_ACTIVE_TAB = None


def find_fb_tab():
    """Pin ONE fb tab for the whole session — evals/clicks on different
    tabs silently no-op (the multi-login-tab scatter bug)."""
    global _ACTIVE_TAB
    if _ACTIVE_TAB:
        for t in tabs():
            if t.get("id") == _ACTIVE_TAB and t.get("type") == "page":
                return t
        _ACTIVE_TAB = None
    cands = [t for t in tabs() if t.get("type") == "page"
             and (_host_is(t.get("url", ""), "facebook.com")
                  or _host_is(t.get("url", ""), "fb.com"))]
    pick = None
    # prefer the plain /login/ surface — login.php?skip_api_login is the
    # app-login interstitial whose 2FA hand-off breaks on scripted submits
    for t in cands:
        if "/login/" in t.get("url", "") and "skip_api_login" not in t.get("url",""):
            pick = t
            break
    if not pick:
        for t in cands:
            if "login" in t.get("url", ""):
                pick = t
                break
    pick = pick or (cands[0] if cands else None)
    if pick:
        _ACTIVE_TAB = pick.get("id")
    return pick


def goto_login():
    """Force the pinned tab onto the plain login page."""
    t = find_fb_tab()
    if not t:
        return
    u = t.get("url", "")
    if "/login/" in u and "skip_api_login" not in u:
        return
    ws = t["webSocketDebuggerUrl"]
    cdp_send(ws, "Page.navigate",
             {"url": "https://www.facebook.com/login/"})


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


def cdp_send(ws_url, method, params=None, timeout=20, _id=1):
    from websockets.sync.client import connect
    with connect(ws_url, max_size=None) as ws:
        ws.send(json.dumps({"id": _id, "method": method,
                            "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            m = json.loads(ws.recv(timeout=timeout))
            if m.get("id") == _id:
                return m.get("result", {})
    return {}


def trusted_click_expr(patterns):
    """Find element center coords for trusted mouse click. Returns {x,y,label}
    or {err}. Meta UI ignores synthetic .click() on key controls."""
    return fb_eval("""(()=>{
      const pats=%s.map(p=>new RegExp(p,'i'));
      const els=[...document.querySelectorAll(
        'button,[role=button],input[type=submit],div[role=button],a,div[role=link]')];
      const vis=els.filter(x=>x.getClientRects().length>0);
      for(const re of pats){
        const b=vis.find(x=>re.test(
          (x.innerText||x.value||x.getAttribute('aria-label')||'').trim()));
        if(b){b.scrollIntoView({block:'center'});
          const r=b.getBoundingClientRect();
          return{x:r.left+r.width/2,y:r.top+r.height/2,
                 label:(b.innerText||b.value||'').trim().slice(0,60)};}
      }
      return{err:'notfound'};
    })()""" % json.dumps(patterns))


def trusted_click(patterns):
    t = find_fb_tab()
    if not t:
        return {"err": "no fb tab"}
    ws = t["webSocketDebuggerUrl"]
    box = trusted_click_expr(patterns)
    if not isinstance(box, dict) or "err" in box or "x" not in box:
        return box if isinstance(box, dict) else {"err": str(box)}
    x, y = box["x"], box["y"]
    for evt in ("mousePressed", "mouseReleased"):
        cdp_send(ws, "Input.dispatchMouseEvent", {
            "type": evt, "x": x, "y": y, "button": "left", "clickCount": 1})
    return {"clicked": box.get("label", "")}


def click_text(patterns):
    # trusted mouse events first — Meta profile-picker/consent controls
    # ignore synthetic .click()
    r = trusted_click(patterns)
    if isinstance(r, dict) and r.get("clicked"):
        return r
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
    if "use another profile" in b or "create new account" in b:
        return "profilepicker"
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
            goto_login()
            time.sleep(4)
            # dismiss cookie/consent banner — it overlays and eats clicks
            fb_eval("""(()=>{const b=[...document.querySelectorAll(
              'button,[role=button],div[role=button]')].find(x=>
              x.getClientRects().length>0&&/allow.*cookie|accept/i
              .test(x.innerText||''));if(b)b.click();return 1})()""")
            time.sleep(1)
            # scope EVERYTHING to the form that owns the email input —
            # the page carries a second decoy form + nav 'Log In' links
            r = fb_eval("""(()=>{
              const f=[...document.querySelectorAll('form')].find(x=>
                x.querySelector('input[name=email],input[type=email],'
                +'input[name=identifier]'));
              if(!f)return{err:'noform'};
              const set=(el,v)=>{if(!el)return 0;
                const s=Object.getOwnPropertyDescriptor(
                  HTMLInputElement.prototype,'value').set;
                s.call(el,v);
                el.dispatchEvent(new Event('input',{bubbles:true}));
                el.dispatchEvent(new Event('change',{bubbles:true}));
                return 1};
              const e=f.querySelector('input[name=email],input[type=email],'
                +'input[name=identifier]');
              const p=f.querySelector('input[name=pass],input[type=password]');
              return{email:set(e,%s),pass:set(p,%s)};
            })()""" % (json.dumps(email), json.dumps(password)))
            print("  fill:", r)
            time.sleep(1)
            r = fb_eval("""(()=>{
              const f=[...document.querySelectorAll('form')].find(x=>
                x.querySelector('input[name=email],input[type=email],'
                +'input[name=identifier]'));
              if(!f)return{err:'noform'};
              if(typeof f.requestSubmit==='function'){f.requestSubmit();
                return{submitted:'requestSubmit'}}
              const b=f.querySelector('button[type=submit],input[type=submit]');
              if(b){b.click();return{submitted:'click'}}
              return{err:'nosubmit'};
            })()""")
            print("  submit:", r)
            time.sleep(6)
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
        elif state == "profilepicker":
            # the profile-tile "Continue" is a dead end for automation
            # (device-bound credential) — "Use another profile" opens the
            # plain email/pass form, verified live
            r = click_text([r"use another profile"])
            if not (isinstance(r, dict) and r.get("clicked")):
                r = click_text([r"themistoklis", r"baltzakis", r"continue"])
            print("  profilepick:", r)
            time.sleep(4)
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
