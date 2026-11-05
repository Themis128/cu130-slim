#!/usr/bin/env python3
"""cdp_eval.py — WSL-side driver that runs JS in a Chrome tab on Windows.

Pushes a self-contained CDP evaluator to Windows via win_agent /exec —
installs websocket-client on first run, connects to localhost:9222,
runs Runtime.evaluate in the tab matching --url-contains.

  cdp_eval.py --url-contains developers/apps/265077368 --js "document.title"
  cdp_eval.py --url-contains developers --js "document.querySelector('button').click()"

Requires the win_agent reachable at WIN_AGENT_URL (default
http://172.22.144.1:8765). Chrome must run with --remote-debugging-port=9222.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request

GATEWAY = "172.22.144.1"
AGENT = f"http://{GATEWAY}:8765"
TOKEN = ""  # WIN_AGENT_TOKEN

WIN_SCRIPT = r'''
import json, sys, urllib.request
try:
    import websocket
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "websocket-client", "-q"])
    import websocket

URL_PART, JS = sys.argv[1], sys.argv[2]
if JS.startswith("__B64__"):
    import base64
    JS = base64.b64decode(JS[7:]).decode()
tabs = json.loads(urllib.request.urlopen(
    "http://localhost:9222/json/list", timeout=10).read())
tab = next((t for t in tabs if t.get("type") == "page"
            and URL_PART in t.get("url", "")), None)
if not tab:
    print(json.dumps({"ok": False, "err": "no tab matching " + URL_PART}))
    sys.exit(0)
ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=20,
                                 suppress_origin=True)
ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                    "params": {"expression": JS, "returnByValue": True}}))
while True:
    m = json.loads(ws.recv())
    if m.get("id") == 1:
        r = m.get("result", {})
        exc = r.get("exceptionDetails", {}).get("exception", {}).get("description")
        print(json.dumps({"ok": exc is None,
                          "value": r.get("result", {}).get("value"),
                          "err": exc}))
        break
ws.close()
'''


def _agent(path, body=None, timeout=60):
    req = urllib.request.Request(
        AGENT + path,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json",
                 **({"X-Agent-Token": TOKEN} if TOKEN else {})},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url-contains", required=True)
    p.add_argument("--js", required=True)
    a = p.parse_args()
    # write the evaluator to Windows temp via base64, then run it
    b64 = base64.b64encode(WIN_SCRIPT.encode()).decode()
    _agent("/exec", {"cmd": f'powershell -c "[IO.File]::WriteAllText(\'C:\\\\tmp\\\\cdp_eval_win.py\', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(\'{b64}\')))"'})
    js_b64 = base64.b64encode(a.js.encode()).decode()
    out = _agent("/exec", {"cmd": (
        f'python C:\\tmp\\cdp_eval_win.py "{a.url_contains}" '
        f'"__B64__{js_b64}"')})
    print(out.get("out", "")[:2000])


if __name__ == "__main__":
    main()
