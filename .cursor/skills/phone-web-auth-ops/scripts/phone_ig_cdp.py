#!/usr/bin/env python3
"""phone_ig_cdp — phone-Chrome CDP driver for Instagram/Meta web lanes.

Runs on WINDOWS (py -3) — WSL cannot reach the adb forward (it binds
Windows localhost only). Prereq:

    adb -s <phone>:5555 forward tcp:9222 localabstract:chrome_devtools_remote

Subcommands:
    list                        — print tabs (id, url)
    probe                       — find live instagram.com tab; print URL + ds_user_id
    eval <js>                   — Runtime.evaluate on the live IG tab
    nav <url>                   — navigate live IG tab
    tap-text <text>             — tap element whose innerText matches (trusted input events)
    fill-login <user> <pass>    — fill IG login form via native setter + tap "Log in"
    consent                     — tap Allow/Continue on the OAuth consent page
    code <digits>               — focus the code input and insert the code
    cookies                     — dump instagram.com cookies of live tab

Design notes (all learned the hard way — do not regress):
  * JS .click() does NOT fire Bloks/React handlers. Use CDP
    Input.dispatchTouchEvent (touchstart+touchend) + dispatchMouseEvent
    (press+release) at CSS coords. See tap().
  * Background tabs freeze their renderers — websocket connects but
    Runtime.evaluate never returns. Always foreground Chrome on the
    phone first, or navigate a different live tab.
  * Input.insertText must only be used AFTER the field is focused
    (JS focus() or a real tap). Never combine with the value setter —
    that produces doubled text ("cloudless.grcloudless.gr").
  * React/Bloks controlled inputs need the native property setter +
    dispatched input event to commit a value — direct .value writes
    are invisible to React state.
"""
import argparse
import json
import time
import urllib.request

import websocket

import os

CDP = "http://localhost:" + os.environ.get("PHONE_CDP_PORT", "9222")


def tabs():
    return json.load(urllib.request.urlopen(CDP + "/json", timeout=10))


from urllib.parse import urlparse


def _host_match(url, domain):
    host = urlparse(url).hostname or ""
    return host == domain or host.endswith("." + domain)


def live_tab(domain="instagram.com"):
    """First responsive page target whose HOST is (a subdomain of) domain —
    substring matching would trust attacker URLs embedding the domain."""
    cands = [t for t in tabs()
             if t.get("type") == "page" and _host_match(t.get("url", ""), domain)]
    for t in cands:
        try:
            ws = websocket.create_connection(
                t["webSocketDebuggerUrl"], suppress_origin=True, timeout=8)
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": "1"}}))
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == 1:
                    break
            ws.close()
            return t
        except Exception:
            continue
    return None


class Tab:
    def __init__(self, t):
        self.ws = websocket.create_connection(
            t["webSocketDebuggerUrl"], suppress_origin=True, timeout=30)
        self._id = 0

    def cmd(self, method, params=None):
        self._id += 1
        self.ws.send(json.dumps(
            {"id": self._id, "method": method, "params": params or {}}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self._id:
                return r

    def eval(self, expr):
        r = self.cmd("Runtime.evaluate", {"expression": expr,
                                          "returnByValue": True})
        return r.get("result", {}).get("result", {}).get("value")

    def nav(self, url, wait=9):
        self.cmd("Page.navigate", {"url": url})
        time.sleep(wait)

    def tap(self, x, y):
        for ev in ("touchStart", "touchEnd"):
            self.cmd("Input.dispatchTouchEvent",
                     {"type": ev, "touchPoints": [{"x": x, "y": y, "id": 0}]})
        for et in ("mousePressed", "mouseReleased"):
            self.cmd("Input.dispatchMouseEvent",
                     {"type": et, "x": x, "y": y, "button": "left",
                      "clickCount": 1})

    def tap_text(self, text):
        v = self.eval(
            "(()=>{const els=[...document.querySelectorAll('a,button,"
            "div[role=button],span,div')].filter(e=>(e.innerText||'')"
            ".trim().startsWith(%s)&&e.offsetParent);"
            "if(!els.length)return 'none';"
            "els[els.length-1].scrollIntoView({block:'center'});"
            "const b=els[els.length-1].getBoundingClientRect();"
            "return JSON.stringify({x:b.x+b.width/2,y:b.y+b.height/2});})()"
            % json.dumps(text))
        if v == "none":
            return False
        xy = json.loads(v)
        time.sleep(0.5)
        self.tap(xy["x"], xy["y"])
        return True

    def fill_login(self, user, password):
        """Native-setter fill (single write per field — never mixes insertText)."""
        v = self.eval(
            "(()=>{const u=document.querySelector('input[name=username]');"
            "const p=document.querySelector('input[name=password]');"
            "if(!u||!p)return 'noform';"
            "const s=Object.getOwnPropertyDescriptor("
            "Object.getPrototypeOf(u),'value').set;"
            "s.call(u,%s);u.dispatchEvent(new Event('input',{bubbles:true}));"
            "s.call(p,%s);p.dispatchEvent(new Event('input',{bubbles:true}));"
            "return u.value+'/plen='+p.value.length;})()"
            % (json.dumps(user), json.dumps(password)))
        return v

    def state(self):
        return self.eval(
            "location.href+' || '+(document.body"
            "?document.body.innerText.slice(0,350):'nobody')")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action")
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()

    if a.action == "list":
        for t in tabs():
            if t.get("type") == "page":
                print(t["id"][:8], t["url"][:110])
        return

    domains = ["threads.net", "instagram.com"] if a.action == "consent" \
        else ["instagram.com", "threads.net", "threads.com"]
    t = None
    for d in domains:
        t = live_tab(d)
        if t:
            break
    if not t:
        print("NO LIVE TAB for", domains); return
    tab = Tab(t)

    if a.action == "probe":
        print("URL:", tab.eval("location.href"))
        print("COOKIE:", tab.eval("document.cookie"))
        print(tab.state())
    elif a.action == "eval":
        print(tab.eval(" ".join(a.args)))
    elif a.action == "nav":
        tab.nav(a.args[0])
        print(tab.state())
    elif a.action == "tap-text":
        ok = tab.tap_text(" ".join(a.args))
        print("tapped" if ok else "not found")
        time.sleep(6)
        print(tab.state())
    elif a.action == "fill-login":
        print(tab.fill_login(a.args[0], a.args[1]))
        time.sleep(1)
        print("submit:", "tapped" if tab.tap_text("Log in") else "no button")
        time.sleep(6)
        print(tab.state())
    elif a.action == "consent":
        for label in ("Allow", "Continue", "Yes", "Authorize"):
            if tab.tap_text(label):
                print("tapped:", label)
                break
        time.sleep(8)
        print(tab.state())
    elif a.action == "code":
        tab.eval("document.querySelector('input').focus()")
        time.sleep(0.7)
        tab.cmd("Input.insertText", {"text": a.args[0]})
        time.sleep(1)
        print("field:", tab.eval("document.querySelector('input').value"))
    elif a.action == "cookies":
        r = tab.cmd("Network.getCookies",
                    {"urls": ["https://www.instagram.com/",
                              "https://www.threads.com/"]})
        for c in r.get("result", {}).get("cookies", []):
            print("%s\t%s\t%s" % (c.get("domain"), c.get("name"),
                                    c.get("value", "")[:40]))


if __name__ == "__main__":
    main()
