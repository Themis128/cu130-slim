#!/usr/bin/env python3
"""cdp_shot.py — capture a page screenshot from the user's real Chrome via
win_agent /exec -> CDP Page.captureScreenshot (no window focus needed).

  cdp_shot.py --url-contains social.cloudless.gr --out /tmp/frame.png
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request

GATEWAY = "172.22.144.1"
AGENT = f"http://{GATEWAY}:8765"

WIN_SCRIPT = r'''
import base64, json, sys, urllib.request
try:
    import websocket
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "websocket-client", "-q"])
    import websocket

URL_PART = sys.argv[1]
CDP = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:9229"
tabs = json.loads(urllib.request.urlopen(
    CDP + "/json/list", timeout=10).read())
tab = next((t for t in tabs if t.get("type") == "page"
            and URL_PART in t.get("url", "")), None)
if not tab:
    print(json.dumps({"ok": False, "err": "no tab matching " + URL_PART}))
    sys.exit(0)
ws = websocket.create_connection(tab["webSocketDebuggerUrl"],
                                 timeout=30, suppress_origin=True)
ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
ws.recv()
ws.send(json.dumps({"id": 2, "method": "Page.captureScreenshot",
                    "params": {"format": "jpeg", "quality": 80}}))
while True:
    m = json.loads(ws.recv())
    if m.get("id") == 2:
        # write to windows temp; WSL reads via /mnt/c
        import os
        os.makedirs(r"C:\tmp", exist_ok=True)
        with open(r"C:\tmp\cdp_shot.jpg", "wb") as f:
            f.write(base64.b64decode(m["result"]["data"]))
        print(json.dumps({"ok": True}))
        break
ws.close()
'''


def _agent(cmd: str) -> dict:
    req = urllib.request.Request(
        f"{AGENT}/exec", data=json.dumps({"cmd": cmd}).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url-contains", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cdp", default="http://localhost:9229")
    a = ap.parse_args()

    b64 = base64.b64encode(WIN_SCRIPT.encode()).decode()
    _agent(
        "powershell -c \"[IO.File]::WriteAllText('C:\\\\tmp\\\\cdp_shot_win.py',"
        f" [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')))\"")

    out = _agent(
        f'python C:\\\\tmp\\\\cdp_shot_win.py "{a.url_contains}" "{a.cdp}"')
    res = json.loads(out.get("out", "{}").strip() or "{}")
    if not res.get("ok"):
        print(json.dumps(res))
        sys.exit(1)

    # read back via the WSL mount
    src = "/mnt/c/tmp/cdp_shot.jpg"
    import shutil
    shutil.copy(src, a.out)
    print(json.dumps({"ok": True, "out": a.out}))


if __name__ == "__main__":
    main()
