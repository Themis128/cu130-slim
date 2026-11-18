#!/usr/bin/env python3
"""li_cdp — drive LinkedIn through the Windows debug Chrome (:9229).

Fallback lane for when linkedin-browser-sidecar (:9225) is in 429
cooldown. Same session family as linkedin-devapp-ops / portal-ops —
the chrome-debug-profile holds the logged-in LinkedIn session used for
developer-portal work.

  python3 li_cdp.py status                     # classify feed state
  python3 li_cdp.py nav <url> [wait_s]         # navigate current/new tab
  python3 li_cdp.py eval <js>                  # Runtime.evaluate -> value
  python3 li_cdp.py text                       # document.body.innerText
  python3 li_cdp.py click-text <label>         # scroll+click first match
  python3 li_cdp.py type <selector> <text>     # native setter + input events
  python3 li_cdp.py shot <path.png>            # Page.captureScreenshot
  python3 li_cdp.py tabs                       # list page targets
  python3 li_cdp.py close <target_substr>      # close a tab by url match

Env: CDP_URL (default tries " + WSL_HOST_IP + ":9229 then localhost:9229).
"""
import base64
import json
import os
import sys
import time
import urllib.request

import websocket
import subprocess

# WSL host IP — the Windows gateway moves subnets on vSwitch rebuilds
# (172.22.144.1 -> 192.168.96.1 observed 2026-11-18). Resolve live.
try:
    WSL_HOST_IP = os.environ.get("WSL_HOST_IP") or subprocess.run(
        ["sh", "-c", "ip route | awk '/default/ {print $3; exit}'"],
        capture_output=True, text=True, timeout=5).stdout.strip() \
        or "172.22.144.1"
except Exception:
    WSL_HOST_IP = "172.22.144.1"


CDP_CANDIDATES = [os.environ["CDP_URL"]] if os.environ.get("CDP_URL") else [
    "http://" + WSL_HOST_IP + ":9229", "http://localhost:9229"]
CDP = None


def _connect():
    global CDP
    for base in CDP_CANDIDATES:
        try:
            urllib.request.urlopen(base + "/json/version", timeout=5).read()
            CDP = base
            return base
        except Exception:
            continue
    sys.exit("CDP :9229 unreachable on all candidates")


def _http(path):
    if CDP is None:
        _connect()
    return json.loads(urllib.request.urlopen(CDP + path, timeout=10).read())


def tabs():
    return [t for t in _http("/json/list") if t.get("type") == "page"]


class Li:
    def __init__(self, match=None):
        _connect()
        ts = tabs()
        if not ts:
            sys.exit("no page targets on :9229")
        tab = (next((t for t in ts if match and match in t["url"]), None)
               or next((t for t in ts if "linkedin.com" in t["url"]), None)
               or ts[-1])
        self.tab = tab
        self.ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], timeout=25, suppress_origin=True)
        self.i = 0
        # strip HeadlessChrome from UA if present (portal bot checks)
        try:
            ua = self.ev("navigator.userAgent") or ""
            if "Headless" in ua:
                clean = ua.replace("HeadlessChrome", "Chrome")
                self.send("Network.enable")
                self.send("Network.setUserAgentOverride",
                          {"userAgent": clean})
        except Exception:
            pass

    def send(self, m, p=None):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": m,
                                 "params": p or {}}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.i:
                return r

    def ev(self, expr):
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True,
                       "awaitPromise": True})
        res = r.get("result", {}).get("result", {})
        if "value" in res:
            return res["value"]
        return res.get("description")

    def nav(self, url, wait=10):
        self.send("Page.enable")
        self.send("Page.navigate", {"url": url})
        time.sleep(wait)

    def click_xy(self, x, y):
        for ty in ("mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent",
                      {"type": ty, "x": x, "y": y,
                       "button": "left", "clickCount": 1})

    def click_text(self, label, sleep=5):
        r = self.ev(f"""(()=>{{
          const pat={json.dumps(label.lower())}
          const els=[...document.querySelectorAll(
            'a,button,[role=button],input[type=submit],span,div')]
          const b=els.find(e=>{{const t=(e.innerText||e.value||'')
            .trim().toLowerCase();return t===pat||t.startsWith(pat)}})
          if(!b)return null;b.scrollIntoView({{block:'center'}})
          const r=b.getBoundingClientRect()
          return [r.left+r.width/2,r.top+r.height/2]}})()""")
        if not r:
            print("no match:", label)
            return False
        self.click_xy(r[0], r[1])
        time.sleep(sleep)
        return True

    def type_text(self, sel, text):
        return self.ev(f"""(()=>{{const t=document.querySelector({json.dumps(sel)})
          ;if(!t)return 0;t.focus()
          const s=Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype,'value').set
          s.call(t,{json.dumps(text)})
          t.dispatchEvent(new Event('input',{{bubbles:true}}))
          t.dispatchEvent(new Event('change',{{bubbles:true}}))
          t.dispatchEvent(new KeyboardEvent('keydown',{{key:'Enter',
            bubbles:true}}))
          return 1}})()""")

    def shot(self, path):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        data = r.get("result", {}).get("data")
        if data:
            open(path, "wb").write(base64.b64decode(data))
            print(path)

    def classify(self):
        info = self.ev("JSON.stringify({url:location.href,"
                       "title:document.title,"
                       "body:document.body?document.body.innerText"
                       ".slice(0,400):''})")
        d = json.loads(info) if isinstance(info, str) else {}
        low = ((d.get("body") or "") + " " + (d.get("title") or "")).lower()
        if "429" in low or "too many requests" in low:
            state = "429"
        elif "authwall" in low or "sign in" in low or "join linkedin" in low:
            state = "loginwall"
        elif "checkpoint" in low or "verify your identity" in low:
            state = "checkpoint"
        elif "feed" in (d.get("url") or "") or "linkedin.com" in \
                (d.get("url") or ""):
            state = "ok"
        else:
            state = "unknown"
        return {**d, "state": state}


def open_tab(url, wait=12):
    _connect()
    ver = json.loads(urllib.request.urlopen(CDP + "/json/version",
                                           timeout=8).read())
    bws = websocket.create_connection(ver["webSocketDebuggerUrl"],
                                      timeout=20, suppress_origin=True)
    bws.send(json.dumps({"id": 1, "method": "Target.createTarget",
                         "params": {"url": url, "background": True}}))
    bws.recv()
    time.sleep(wait)
    return Li(match=url.split("/")[2] if "://" in url else None)


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 1
    cmd = a[0]
    if cmd == "tabs":
        for t in tabs():
            print(f"{t['id'][:8]}  {t['url'][:100]}")
        return 0
    if cmd == "open":
        li = open_tab(a[1], int(a[2]) if len(a) > 2 else 12)
        print(json.dumps(li.classify(), indent=1)[:800])
        return 0
    if cmd == "close":
        for t in tabs():
            if a[1] in t["url"]:
                urllib.request.urlopen(
                    CDP + "/json/close/" + t["id"], timeout=5).read()
                print("closed", t["url"][:80])
        return 0
    li = Li(match=a[1] if cmd == "eval-tab" else None)
    if cmd == "status":
        try:
            cd = json.load(open(os.path.expanduser(
                "~/.local/state/li-429-cooldown.json")))
            rem = int(cd.get("until", 0) - time.time())
            if rem > 0:
                print(f"[cooldown] 429 window ~{rem//60}m{rem%60}s remaining "
                      f"(detected {time.strftime('%H:%M', time.localtime(cd.get('detected_at', 0)))})")
        except Exception:
            pass
        li.nav("https://www.linkedin.com/feed/", 10)
        # navigation can drop the target ws — reconnect before evaluating
        try:
            print(json.dumps(li.classify(), indent=1)[:1500])
        except Exception:
            li = Li(match="linkedin.com")
            print(json.dumps(li.classify(), indent=1)[:1500])
    elif cmd == "nav":
        li.nav(a[1], int(a[2]) if len(a) > 2 else 10)
        print(li.ev("location.href"))
    elif cmd == "eval":
        print(li.ev(a[1]))
    elif cmd == "text":
        print(li.ev("document.body?document.body.innerText:''") or "")
    elif cmd == "click-text":
        li.click_text(a[1])
    elif cmd == "type":
        print("typed" if li.type_text(a[1], a[2]) else "no element")
    elif cmd == "shot":
        li.shot(a[1])
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
