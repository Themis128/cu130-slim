#!/usr/bin/env python3
"""cdp_common — shared Chrome DevTools Protocol client for all CDP tools.

auto-ensure: probes :9222, and if down runs consent-root-ops/cdp_ensure
(scheduled task -> cmd start -> powershell) to relaunch debug Chrome with
--remote-allow-origins=*.

Import from any skill script:

    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                    "_lib"))
    from cdp_common import CdpClient, ensure_cdp

    ensure_cdp()                      # raises SystemExit if it can't
    cdp = CdpClient()                 # first page target
    cdp.ev("document.body.innerText")
    cdp.click_xy(x, y)
    cdp.upload_file("/path/to.pdf")   # DOM.setFileInputFiles injection
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

CDP = "http://localhost:9222"
ENSURE = os.path.join(os.path.dirname(__file__), "..",
                      "consent-root-ops", "scripts", "cdp_ensure.py")


def ensure_cdp(launch=True):
    try:
        v = json.loads(urllib.request.urlopen(
            f"{CDP}/json/version", timeout=5).read())
        return v.get("Browser", "")
    except Exception:
        pass
    if not launch:
        sys.exit(f"CDP :9222 unreachable — launch debug Chrome")
    r = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.normpath(ENSURE)],
                       capture_output=True, text=True, timeout=60)
    for _ in range(12):
        try:
            v = json.loads(urllib.request.urlopen(
                f"{CDP}/json/version", timeout=4).read())
            return v.get("Browser", "")
        except Exception:
            time.sleep(3)
    sys.exit("CDP :9222 unreachable after relaunch attempts")


def targets(t="page"):
    return [x for x in json.load(urllib.request.urlopen(
        f"{CDP}/json", timeout=8)) if x.get("type") == t]


class CdpClient:
    def __init__(self, match=None):
        tabs = targets()
        if not tabs:
            sys.exit("no page targets on :9222")
        tab = (next((t for t in tabs if match and match in t["url"]), None)
               or tabs[0])
        self.ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
        self.i = 0

    def send(self, m, p=None):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": m, "params": p or {}}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.i:
                return r

    def ev(self, x, ctx=None):
        p = {"expression": x, "returnByValue": True}
        if ctx:
            p["contextId"] = ctx
        return self.send("Runtime.evaluate", p).get("result", {}) \
                 .get("result", {}).get("value")

    def nav(self, url, wait=8):
        self.send("Page.enable")
        self.send("Page.navigate", {"url": url})
        time.sleep(wait)

    def click_xy(self, x, y):
        for ty in ("mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent",
                      {"type": ty, "x": x, "y": y,
                       "button": "left", "clickCount": 1})

    def click_label(self, label, sleep=6):
        r = self.ev(f"""(()=>{{
            const pat={json.dumps(label.lower())};
            const els=[...document.querySelectorAll('a,button,div[role=button],span,div')];
            const b=els.find(e=>{{const t=(e.innerText||'').trim().toLowerCase();
                return t===pat||t.startsWith(pat)}});
            if(!b)return null;
            b.scrollIntoView({{block:'center'}});
            const r=b.getBoundingClientRect();
            return [r.left+r.width/2,r.top+r.height/2]}})()""")
        if not r:
            return False
        self.click_xy(r[0], r[1])
        time.sleep(sleep)
        return True

    def upload_file(self, path):
        self.send("Page.setInterceptFileChooserDialog", {"enabled": True})
        self.send("DOM.enable")
        doc = self.send("DOM.getDocument", {"depth": -1}).get("result", {})
        q = self.send("DOM.querySelector", {
            "nodeId": doc.get("root", {}).get("nodeId"),
            "selector": "input[type=file]"})
        nid = q.get("result", {}).get("nodeId")
        if not nid:
            return False
        self.send("DOM.setFileInputFiles", {"nodeId": nid, "files": [path]})
        return True
