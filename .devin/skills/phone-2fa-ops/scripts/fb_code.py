#!/usr/bin/env python3
"""fb_code.py — pull a Meta/WhatsApp 2FA code straight off the phone.

Reads the notification shade via `adb dumpsys notification --noredact`,
extracts the newest "Use NNNNNN for two-factor authentication" line,
and optionally types it into a CDP-driven browser field.

    python3 fb_code.py                 # print the newest code
    python3 fb_code.py --latest-of N   # keep polling for N seconds
    python3 fb_code.py --fill          # also fill+submit the 2FA field via CDP

Requires: adb on PATH (or ADB env), a connected device
(serial ADB_SERIAL, default 192.168.1.77:5555), and for --fill the
CDP forwarder at CDP_URL (default http://100.83.120.93:9223) with the
two_factor tab open.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ADB = os.environ.get("ADB", "adb")
SER = os.environ.get("ADB_SERIAL", "192.168.1.77:5555")
CDP = os.environ.get("CDP_URL", "http://100.83.120.93:9223")
CODE_RE = re.compile(r"Use\s+(\d{4,8})\s+for two-factor")


def sh(*a: str) -> str:
    r = subprocess.run([ADB, "-s", SER, "shell", *a],
                       capture_output=True, text=True, timeout=30)
    return r.stdout + r.stderr


def wake() -> None:
    sh("input keyevent KEYCODE_WAKEUP")
    sh("input keyevent 82")


def codes() -> list[str]:
    out = sh("dumpsys notification --noredact")
    return CODE_RE.findall(out)


def latest_code(timeout_s: int = 0) -> str | None:
    seen = set()
    deadline = time.time() + timeout_s
    while True:
        found = codes()
        fresh = [c for c in found if c not in seen]
        if fresh:
            return fresh[-1]
        seen.update(found)
        if time.time() > deadline:
            return None
        time.sleep(5)


def fill_cdp(code: str) -> bool:
    try:
        targets = json.load(urllib.request.urlopen(CDP + "/json", timeout=8))
    except Exception:
        return False
    t = next((x for x in targets if "two_factor" in x.get("url", "")), None)
    if not t:
        return False
    try:
        import websocket
    except ImportError:
        return False
    ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=15,
                                     suppress_origin=True)
    expr = """(() => {
      const inp=[...document.querySelectorAll('input')].find(i=>['text','number','tel'].includes(i.type)||i.name==='code');
      if(!inp) return 'no-input';
      const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
      s.call(inp,'%s'); inp.dispatchEvent(new Event('input',{bubbles:true}));
      const b=[...document.querySelectorAll('button,div[role=button],span')].find(x=>/^Continue$/i.test((x.innerText||'').trim()));
      if(b) b.click(); return 'submitted';
    })()""" % code
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == 1:
            ws.close()
            return r.get("result", {}).get("result", {}).get("value") == "submitted"


def main() -> int:
    fill = "--fill" in sys.argv
    to = 120 if "--latest" in sys.argv else 0
    wake()
    code = latest_code(to)
    if not code:
        print("no code found"); return 1
    print(code)
    if fill:
        ok = fill_cdp(code)
        print("fill:", ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
