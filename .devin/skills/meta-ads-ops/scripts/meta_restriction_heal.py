#!/usr/bin/env python3
"""Meta restriction-remediation driver — the full chain via the USER'S REAL
CHROME (CDP :9222
consent-root-ops start-chrome-debug.bat must have run).

Commands:
  status                 dump current tab (title|url|text)
  shot                   screenshot -> C:/Users/tbaltzakis/Downloads/meta_heal.png
  nav <url>              navigate + status
  step                   click the most likely next action
  login <email>          fill FB login form (password from FB_PASSWORD env or prompt)
  twofa                  open 2FA page -> pick the Facebook profile row ->
                         screenshot whatever the picker shows (QR / device block)
  confirm <6-digit code> enter a TOTP/SMS code into the open 2FA form
  totp                   print a fresh TOTP code from FB_TOTP_SECRET env

Chain (verified 2026-10-29):
  login -> /id/hub 'Get started' -> 'Protect your account' ->
  accountscenter 2FA -> pick profile -> authenticator app QR -> enter TOTP
  -> done
  then business verification -> NEW ad account -> WhatsApp.

Known gates:
  - New-device hold: 'you can't make this change at the moment' — approve
    the login notification on a TRUSTED device (phone FB app) and/or use
    the session normally until Meta ages it in. The FB phone app's own
    Settings path is the trusted-device alternative but its UI is deep.
  - iframe-walled accountscenter pages: body.innerText is empty — click
    via real Input.dispatchMouseEvent on element rects, not DOM clicks.
"""
import base64
import json
import os
import sys
import time
import urllib.request

import websocket

CDP = "http://localhost:9222"
SHOT = "C:/Users/tbaltzakis/Downloads/meta_heal.png"
ID_HUB = "https://www.facebook.com/id/hub"
TWOFA = "https://accountscenter.facebook.com/password_and_security/two_factor"


def conn():
    tabs = json.load(urllib.request.urlopen(f"{CDP}/json", timeout=8))
    pages = [t for t in tabs if t.get("type") == "page"]
    ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"],
                                     timeout=15, suppress_origin=True)
    i = [0]
    send(ws, i, "Page.enable")
    send(ws, i, "Runtime.enable")
    return ws, i


def send(ws, i, method, params=None):
    i[0] += 1
    ws.send(json.dumps({"id": i[0], "method": method, "params": params or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == i[0]:
            return r


def ev(ws, i, expr):
    r = send(ws, i, "Runtime.evaluate",
             {"expression": expr, "returnByValue": True})
    return r.get("result", {}).get("result", {}).get("value")


def real_click(ws, i, x, y):
    for t in ("mousePressed", "mouseReleased"):
        send(ws, i, "Input.dispatchMouseEvent",
             {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})


def click_text(ws, i, pattern):
    js = f"""(()=>{{const els=[...document.querySelectorAll(
      'div[role=button],button,a,label,input[type=submit]')];
      const el=els.find(e=>/{pattern}/i.test(e.innerText||e.value||''))
      if(!el)return 'none';
      const r=el.getBoundingClientRect()
      return JSON.stringify([r.left+r.width/2,r.top+r.height/2])}})()"""
    v = ev(ws, i, js)
    if not v or v == "none":
        return None
    x, y = json.loads(v)
    real_click(ws, i, x, y)
    return (x, y)


def status(ws, i):
    return ev(ws, i, "document.title+'|'+location.href.slice(0,110)+'||'"
                     "+document.body.innerText.slice(0,1400)")


def shot(ws, i):
    s = send(ws, i, "Page.captureScreenshot", {"format": "png"})
    open(SHOT, "wb").write(base64.b64decode(s["result"]["data"]))
    return SHOT


def nav(ws, i, url, wait=9):
    send(ws, i, "Page.navigate", {"url": url})
    time.sleep(wait)


def login(ws, i, email):
    pw = os.environ.get("FB_PASSWORD")
    if not pw:
        import getpass
        pw = getpass.getpass("FB password: ")
    nav(ws, i, "https://www.facebook.com/", 6)
    js = f"""(()=>{{
      const em=document.querySelector('input[name=email],input[type=text]')
      const pw=document.querySelector('input[name=pass],input[type=password]')
      if(!em||!pw)return 'no-fields';
      em.focus()
em.value={json.dumps(email)}
em.dispatchEvent(new Event('input',{{bubbles:true}}))
      pw.focus()
pw.value={json.dumps(pw)}
pw.dispatchEvent(new Event('input',{{bubbles:true}}))
      const b=[...document.querySelectorAll('button,input[type=submit]')]
        .find(e=>/log ?in/i.test(e.innerText||e.value||e.name||''))
      if(b){{b.click();return 'submitted'}}return 'no-btn';}})()"""
    print("login:", ev(ws, i, js))
    time.sleep(8)
    print(status(ws, i)[:1200])


def twofa(ws, i):
    nav(ws, i, TWOFA, 10)
    # click the Facebook profile row (real click — iframe-walled page)
    js = """(()=>{const el=[...document.querySelectorAll('*')]
      .find(e=>e.children.length>=1&&(e.innerText||'').includes('Themistoklis Baltzakis')
        &&(e.innerText||'').includes('Facebook')&&(e.innerText||'').length<80)
      if(!el)return 'none';
      const r=el.getBoundingClientRect()
      return JSON.stringify([r.left+r.width/2,r.top+r.height/2])})()"""
    v = ev(ws, i, js)
    if v and v != "none":
        x, y = json.loads(v)
        real_click(ws, i, x, y)
        time.sleep(7)
    print("shot:", shot(ws, i))
    print(status(ws, i)[:1400])


def confirm(ws, i, code):
    js = f"""(()=>{{
      const f=[...document.querySelectorAll('input[type=text],input[type=tel],input')]
      if(!f.length)return 'no-field';
      f[0].focus()
f[0].value='{code}'
      f[0].dispatchEvent(new Event('input',{{bubbles:true}}))
      const b=[...document.querySelectorAll('div[role=button],button')]
        .find(e=>/continue|next|confirm|verify|submit/i.test(e.innerText||''))
      if(b){{b.click();return 'submitted'}}return 'typed-no-btn';}})()"""
    print("confirm:", ev(ws, i, js))
    time.sleep(7)
    print(status(ws, i)[:1400])


def totp():
    import pyotp
    secret = os.environ.get("FB_TOTP_SECRET")
    if not secret:
        print("set FB_TOTP_SECRET")
        return
    print(pyotp.TOTP(secret).now())


def step(ws, i):
    for pat in ["get started", "protect your account", "authenticat",
                "continue", "next", "confirm", "submit", "ok"]:
        if click_text(ws, i, pat):
            print("clicked:", pat)
            time.sleep(6)
            print(status(ws, i)[:1400])
            return
    print("nothing clickable:", status(ws, i)[:1400])


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "totp":
        totp()
        return
    ws, i = conn()
    if cmd == "status":
        print(status(ws, i))
    elif cmd == "shot":
        print("saved:", shot(ws, i))
    elif cmd == "nav":
        nav(ws, i, sys.argv[2])
        print(status(ws, i)[:1400])
    elif cmd == "step":
        step(ws, i)
    elif cmd == "login":
        login(ws, i, sys.argv[2])
    elif cmd == "twofa":
        twofa(ws, i)
    elif cmd == "confirm":
        confirm(ws, i, sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
