#!/usr/bin/env python3
"""phone_cdp.py — drive Chrome on the Galaxy S22+ over CDP.

Runs on the OFFICE Windows host (adb + python3 + websocket-client).
Chrome on Android suspends CDP on background tabs — always create a
FRESH tab (Target.createTarget) or bring Chrome to the foreground before
driving; Runtime.evaluate on a frozen tab just times out.

Usage:
  python phone_cdp.py forward          # adb forward tcp:9555 -> chrome_devtools_remote
  python phone_cdp.py tabs             # list open pages
  python phone_cdp.py new <url>        # open URL in a new (foreground) tab
  python phone_cdp.py nav <substr> <url>   # Page.navigate on the tab whose URL contains substr
  python phone_cdp.py eval <substr> '<js>' # Runtime.evaluate (returns .value)
  python phone_cdp.py shot <substr> <out.png> # CDP screenshot of a tab
  python phone_cdp.py front            # bring Chrome activity to front (thaws tabs)

Env: ADB_SERIAL (default RFCTA0AEKZP), CDP_PORT (default 9555).
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

ADB_SERIAL = os.environ.get("ADB_SERIAL", "RFCTA0AEKZP")
PORT = int(os.environ.get("CDP_PORT", "9555"))
BASE = f"http://127.0.0.1:{PORT}"


def adb(*args):
    return subprocess.run(["adb", "-s", ADB_SERIAL, *args],
                          capture_output=True, text=True, timeout=30)


def forward():
    r = adb("forward", f"tcp:{PORT}", "localabstract:chrome_devtools_remote")
    if r.returncode != 0:
        sys.exit(f"adb forward failed: {r.stderr.strip()}")
    print(f"forwarded tcp:{PORT} -> chrome_devtools_remote on {ADB_SERIAL}")


def front():
    adb("shell", "am", "start", "-n",
        "com.android.chrome/com.google.android.apps.chrome.Main")


def _get(path):
    return json.load(urllib.request.urlopen(BASE + path, timeout=10))


def tabs():
    return [t for t in _get("/json/list") if t.get("type") == "page"]


def pick(substr):
    for t in tabs():
        if substr in t.get("url", ""):
            return t
    sys.exit(f"no tab matching {substr!r}; tabs: {[t['url'][:80] for t in tabs()]}")


class Tab:
    """Minimal CDP client over one page's websocket."""

    def __init__(self, ws_url):
        import websocket  # websocket-client
        self.ws = websocket.create_connection(ws_url, suppress_origin=True,
                                              timeout=30)
        self._id = 0

    def call(self, method, params=None, retries=3):
        for attempt in range(retries):
            self._id += 1
            i = self._id
            self.ws.send(json.dumps({"id": i, "method": method,
                                     "params": params or {}}))
            deadline = time.time() + 30
            try:
                while time.time() < deadline:
                    m = json.loads(self.ws.recv())
                    if m.get("id") == i:
                        if "error" in m:
                            raise RuntimeError(m["error"])
                        return m.get("result", {})
            except Exception as e:
                if attempt == retries - 1:
                    raise
                time.sleep(2)
        return {}

    def evaluate(self, expr):
        r = self.call("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("value")

    def navigate(self, url):
        self.call("Page.navigate", {"url": url})


def new_tab(url):
    ws_url = _get("/json/version")["webSocketDebuggerUrl"]
    t = Tab(ws_url)
    return t.call("Target.createTarget", {"url": url})["targetId"]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "forward":
        forward()
    elif cmd == "front":
        front(); print("chrome front")
    elif cmd == "tabs":
        for t in tabs():
            print(t["url"][:160])
    elif cmd == "new":
        print(new_tab(sys.argv[2]))
    elif cmd == "nav":
        Tab(pick(sys.argv[2])["webSocketDebuggerUrl"]).navigate(sys.argv[3])
        print("nav ok")
    elif cmd == "eval":
        print(Tab(pick(sys.argv[2])["webSocketDebuggerUrl"])
              .evaluate(sys.argv[3]))
    elif cmd == "shot":
        r = Tab(pick(sys.argv[2])["webSocketDebuggerUrl"]) \
            .call("Page.captureScreenshot", {"format": "png"})
        open(sys.argv[3], "wb").write(base64.b64decode(r["data"]))
        print("saved", sys.argv[3])
    else:
        sys.exit(f"unknown cmd {cmd!r}\n{__doc__}")


if __name__ == "__main__":
    main()
