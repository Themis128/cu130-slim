#!/usr/bin/env python3
"""ig_phone_credential_login.py — fresh credential login on phone Chrome.

Handles the instagram.com/oauth/authorize `force_authentication` wall:
even with a live IG session, the OAuth flow demands a FRESH credential
submit. This driver fills INSTAGRAM_USERNAME/INSTAGRAM_PASSWORD on the
login form via real CDP input (click field -> Input.insertText -> real
mouse click on Log in) and reports the next gate.

Verified live 2026-11-26: DOM values alone leave the Bloks "Log in"
button aria-disabled=true; only real key/mouse input flips React state.

Prereqs: ADB_SERIAL (default 100.84.31.85:5555), phone unlocked with
Chrome foregrounded, python websocket-client. Run from WSL.

Usage:
    ig_phone_credential_login.py            # find IG login tab & drive
    ig_phone_credential_login.py --url URL  # open URL first

Exit codes: 0 submitted / 2 no-login-tab / 3 unexpected-page /
            4 still-on-login-wall (bad creds or extra gate — inspect).
Never prints passwords, codes, or tokens.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

ADB_SERIAL = os.environ.get("ADB_SERIAL", "100.84.31.85:5555")
os.environ["ADB_SERIAL"] = ADB_SERIAL  # phone_cdp binds it at import time
REPO = os.path.expanduser("~/cu130-slim")
sys.path.insert(0, os.path.join(
    REPO, ".devin/skills/phone-web-auth-ops/scripts"))
import phone_cdp  # noqa: E402


def load_env() -> dict:
    env = {}
    for line in open(os.path.join(REPO, ".env")):
        m = re.match(r"([A-Z_0-9]+)=(.*)", line.strip())
        if m:
            env[m.group(1)] = m.group(2).strip().strip("\"'")
    return env


def pick_tab():
    tabs = phone_cdp.tabs()
    for t in tabs:
        if "instagram.com" in t.get("url", ""):
            return t
    return None


def center(tab, sel):
    r = tab.evaluate(
        "(()=>{const el=document.querySelector(%s);if(!el)return null;"
        "el.scrollIntoView({block:'center'});const r=el.getBoundingClientRect();"
        "return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2})})()"
        % json.dumps(sel))
    return json.loads(r) if r else None


def click_at(tab, p):
    for typ, extra in (("mouseMoved", {}),
                       ("mousePressed", {"button": "left", "clickCount": 1}),
                       ("mouseReleased", {"button": "left", "clickCount": 1})):
        tab.call("Input.dispatchMouseEvent",
                 {"type": typ, "x": p["x"], "y": p["y"], **extra})
        time.sleep(0.12)


def clear_and_type(tab, sel, text):
    p = center(tab, sel)
    if not p:
        return False
    click_at(tab, p)
    time.sleep(0.4)
    # real Backspace key events — JS .value='' desyncs Bloks state
    for _ in range(40):
        tab.call("Input.dispatchKeyEvent",
                 {"type": "keyDown", "key": "Backspace", "code": "Backspace",
                  "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8})
        tab.call("Input.dispatchKeyEvent",
                 {"type": "keyUp", "key": "Backspace", "code": "Backspace",
                  "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8})
        time.sleep(0.02)
    tab.call("Input.insertText", {"text": text})
    time.sleep(0.4)
    return True


def main() -> int:
    url = (sys.argv[sys.argv.index("--url") + 1]
           if "--url" in sys.argv else None)
    env = load_env()
    user = env.get("INSTAGRAM_USERNAME", "cloudless.gr")
    pw = env.get("INSTAGRAM_PASSWORD")
    if not pw:
        print("INSTAGRAM_PASSWORD missing from .env")
        return 5

    phone_cdp.forward()
    phone_cdp.front()
    if url:
        phone_cdp.new_tab(url)
        time.sleep(6)
    t = pick_tab()
    if not t:
        print("no instagram.com tab on phone Chrome")
        return 2
    tab = phone_cdp.Tab(t["webSocketDebuggerUrl"])
    tab.ws.settimeout(25)
    ev = lambda e: tab.evaluate(e)

    print("tab:", (ev("location.href") or "")[:120])
    has_form = ev("!!document.querySelector('input[name=username]')"
                  "&&!!document.querySelector('input[name=password]')")
    if not has_form:
        print("no credential form on page — already logged in or different gate")
        print("BODY:", (ev("document.body.innerText.slice(0,300)") or "")[:200])
        return 0

    if not clear_and_type(tab, "input[name=username]", user):
        print("username field not found")
        return 3
    if not clear_and_type(tab, "input[name=password]", pw):
        print("password field not found")
        return 3
    print("filled:", ev("JSON.stringify({u:document.querySelector('input[name=username]')?.value,"
                       "pwlen:(document.querySelector('input[name=password]')?.value||'').length})"))

    # real click on the Log in control
    btn = ev("""(()=>{const els=[...document.querySelectorAll(
'div[role=button],button,a')].filter(e=>e.getClientRects().length&&
(e.innerText||'').trim()==='Log in');
const b=els[els.length-1];if(!b)return null;b.scrollIntoView({block:'center'});
const r=b.getBoundingClientRect();
return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2,dis:b.getAttribute('aria-disabled')})})()""")
    print("btn:", btn)
    if not btn:
        return 3
    p = json.loads(btn)
    click_at(tab, p)

    for i in range(15):
        time.sleep(4)
        u = ev("location.href") or ""
        b = (ev("document.body.innerText.slice(0,400)") or "")
        print(f"[{i}] {u[:110]} | {b.replace(chr(10),' | ')[:140]}")
        if not re.search(r"accounts/login|force_authentication", u, re.I):
            print("advanced past login wall")
            return 0
    print("still on login wall after submit — check creds / next gate manually")
    return 4


if __name__ == "__main__":
    sys.exit(main())
