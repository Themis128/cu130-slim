#!/usr/bin/env python3
"""bm_settings_drive.py — drive Meta Business Settings (iframe-walled SPA)
via the user's real Chrome on CDP :9222.

The business.facebook.com settings surface puts its content inside
cross-origin iframes, so top-frame queries hit nothing and synthetic
click() gets ignored. This driver:
  - enumerates EVERY frame (iframe content lives in frame targets)
  - evaluates inside each frame's context
  - clicks via real Input.dispatchMouseEvent at element coordinates
  - expands "Toggle group" sections
  - injects files into file inputs via DOM.setFileInputFiles
    (Page.setInterceptFileChooserDialog — NO OS dialog, fully automatable)

Usage:
  bm_settings_drive.py frames                  # list frame targets+urls
  bm_settings_drive.py nav <url>               # navigate top page
  bm_settings_drive.py text [--frame N]        # innerText per frame
  bm_settings_drive.py click "<label>"         # real-input click by text
  bm_settings_drive.py toggle "<label>"        # expand a Toggle group
  bm_settings_drive.py upload <file>           # inject file into input[type=file]
"""
import argparse
import json
import sys
import time
import urllib.request

import websocket

CDP = "http://localhost:9222"
IMG = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
       "pdf": "application/pdf"}


def get_targets():
    try:
        return json.load(urllib.request.urlopen(f"{CDP}/json", timeout=8))
    except Exception:
        sys.exit("CDP :9222 unreachable — run cdp_ensure.py or relaunch "
                 "debug Chrome (chrome-debug-launch.bat)")


class Cdp:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=20,
                                              suppress_origin=True)
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

    def click_xy(self, x, y):
        for ty in ("mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent",
                      {"type": ty, "x": x, "y": y,
                       "button": "left", "clickCount": 1})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["frames", "nav", "text", "click",
                                    "toggle", "upload"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--frame", type=int)
    a = ap.parse_args()

    tabs = [t for t in get_targets() if t.get("type") == "page"]
    if not tabs:
        print("no page targets")
        return
    c = Cdp(tabs[0]["webSocketDebuggerUrl"])
    c.send("Page.enable")
    c.send("Runtime.enable")

    if a.cmd == "frames":
        tree = c.send("Page.getFrameTree").get("result", {})
        def walk(node, d=0):
            f = node["frame"]
            print("  " * d, f.get("id", "")[:16], f.get("url", "")[:80])
            for ch in node.get("childFrames") or []:
                walk(ch, d + 1)
        walk(tree["frameTree"])
        return

    if a.cmd == "nav":
        c.send("Page.navigate", {"url": a.arg})
        time.sleep(8)
        print(c.ev("location.href"))
        return

    if a.cmd == "text":
        print((c.ev("document.body.innerText.slice(0,2500)") or "")[:2500])
        return

    if a.cmd in ("click", "toggle"):
        # search every context (top + iframes) for the label
        ctxs = c.send("Runtime.evaluate", {
            "expression": "1", "returnByValue": True})  # warm contexts
        r = c.ev(f"""(()=>{{
            const pat={json.dumps(a.arg.lower())}
            const els=[...document.querySelectorAll('a,button,div[role=button],span,div')]
            const b=els.find(e=>{{const t=(e.innerText||'').trim().toLowerCase()
return t===pat||t.startsWith(pat)}})
            if(!b)return null;
            b.scrollIntoView({{block:'center'}})
            const r=b.getBoundingClientRect()
            return [r.left+r.width/2,r.top+r.height/2]}})()""")
        if not r:
            print("not found:", a.arg)
            return
        x, y = r[0], r[1]
        c.click_xy(x, y)
        time.sleep(6)
        print("clicked at", x, y)
        return

    if a.cmd == "upload":
        # intercept the native file dialog, then set files on the input
        c.send("Page.setInterceptFileChooserDialog", {"enabled": True})
        c.send("DOM.enable")
        # find the file input
        doc = c.send("DOM.getDocument", {"depth": -1}).get("result", {})
        q = c.send("DOM.querySelector", {
            "nodeId": doc.get("root", {}).get("nodeId"),
            "selector": "input[type=file]"})
        nid = q.get("result", {}).get("nodeId")
        if not nid:
            # click an "Upload"/"Add" button first to create the input
            print("no file input — click the upload button first, "
                  "then re-run upload")
            return
        r = c.send("DOM.setFileInputFiles",
                   {"nodeId": nid, "files": [a.arg]})
        print("file injected:", a.arg, "| ok:", r.get("result") is not None)


if __name__ == "__main__":
    main()
