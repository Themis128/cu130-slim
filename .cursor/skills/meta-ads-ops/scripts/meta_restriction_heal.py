#!/usr/bin/env python3
"""Meta restriction-remediation stepper — drives the USER'S REAL CHROME
(CDP :9222) through the verified 2026 unblocking chain:

  1. reauth (password)         — facebook.com/id requires re-entry
  2. /id/hub confirm-identity  — 'Protect your account' routes to 2FA
  3. accountscenter 2FA        — authenticator app (QR/secret capture)
  4. business verification     — Business settings > Security Centre
  5. NEW ad account            — old 2021-disabled acct can't be reinstated
                                 (>180d rule) — create fresh in the healthy
                                 portfolio instead

Usage:
  meta_restriction_heal.py status    # which step the flow is at
  meta_restriction_heal.py step      # advance one screen (nav + click hint)
  meta_restriction_heal.py shot      # screenshot current state -> Downloads

Requires Chrome running with --remote-debugging-port=9222
(consent-root-ops start-chrome-debug.bat) and a logged-in FB session.
"""
import base64
import json
import sys
import time
import urllib.request

import websocket

CDP = "http://localhost:9222"
SHOT = "C:/Users/tbaltzakis/Downloads/meta_heal.png"


def ws_send(ws, i, method, params=None):
    ws.send(json.dumps({"id": i[0], "method": method, "params": params or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == i[0]:
            return r


def conn():
    tabs = json.load(urllib.request.urlopen(f"{CDP}/json", timeout=8))
    pages = [t for t in tabs if t.get("type") == "page"]
    ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"], timeout=15)
    i = [0]
    ws_send(ws, i, "Page.enable")
    ws_send(ws, i, "Runtime.enable")
    return ws, i


def ev(ws, i, expr):
    r = ws_send(ws, i, "Runtime.evaluate",
                {"expression": expr, "returnByValue": True})
    return r.get("result", {}).get("result", {}).get("value")


def click_text(ws, i, pattern):
    js = f"""(()=>{{const b=[...document.querySelectorAll(
      'div[role=button],button,a,label,input[type=submit]')]
      .find(e=>/{pattern}/i.test(e.innerText||e.value||''));
      if(!b)return 'none';b.click();return 'clicked:'+(b.innerText||b.value||'').slice(0,40)}})()"""
    return ev(ws, i, js)


def nav(ws, i, url, wait=9):
    ws_send(ws, i, "Page.navigate", {"url": url})
    time.sleep(wait)


def shot(ws, i):
    s = ws_send(ws, i, "Page.captureScreenshot", {"format": "png"})
    open(SHOT, "wb").write(base64.b64decode(s["result"]["data"]))
    return SHOT


def status(ws, i):
    return ev(ws, i,
              "document.title+'|'+location.href.slice(0,110)+'||'+document.body.innerText.slice(0,1200)")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    ws, i = conn()
    if cmd == "shot":
        print("saved:", shot(ws, i)); return
    if cmd == "status":
        print(status(ws, i)); return
    if cmd == "step":
        # generic advance: click the most likely next action on the page
        for pat in ["get started", "continue", "next", "protect your account",
                    "authentication app", "authenticator", "confirm", "submit"]:
            r = click_text(ws, i, pat)
            if r != "none":
                print("clicked:", r)
                time.sleep(6)
                print(status(ws, i)[:1500])
                return
        print("no actionable button — status:")
        print(status(ws, i)[:1500])
        return
    if cmd == "nav":
        nav(ws, i, sys.argv[2])
        print(status(ws, i)[:1500])


if __name__ == "__main__":
    main()
