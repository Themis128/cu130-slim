#!/usr/bin/env python3
"""cdp_relay.py — WSL-side CDP relay through win_agent on Windows :8765.

win_agent binds 0.0.0.0 on Windows but CDP (:9222) stays on Windows
localhost — WSL can't reach it directly. This relay proxies the CDP
HTTP discovery endpoints through the agent's /exec lane:

    cdp_relay.py status            # agent + chrome + cdp health
    cdp_relay.py launch            # launch debug Chrome on Windows
    cdp_relay.py open <url>        # open URL in the REAL Chrome session
    cdp_relay.py tabs              # list open CDP targets

Usage: python3 cdp_relay.py <cmd>
Requires WIN_AGENT_URL (default: http://<default-gw>:8765).
"""
from __future__ import annotations

import json
import sys
import urllib.request
import urllib.parse

GATEWAY = "172.22.144.1"  # WSL default route → Windows host
AGENT = f"http://{GATEWAY}:8765"
TOKEN = ""  # WIN_AGENT_TOKEN if set


def _agent(path, body=None, timeout=15):
    req = urllib.request.Request(
        AGENT + path,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json",
                 **({"X-Agent-Token": TOKEN} if TOKEN else {})},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _win_curl(path, timeout=15):
    """curl localhost:9222 on the Windows side via /exec."""
    out = _agent("/exec", {"cmd": f'curl -s "http://localhost:9222{path}"', "timeout": timeout})
    return out.get("out", "")


def status():
    try:
        h = _agent("/health", timeout=8)
        print("agent:", "up", h.get("py", "")[:40])
    except Exception as e:
        print("agent: DOWN —", type(e).__name__); return
    v = _win_curl("/json/version")
    print("chrome:", "live" if '"Browser"' in v else "no CDP — run launch")


def launch():
    print(_agent("/launch-chrome"))


def tabs():
    print(_win_curl("/json/list")[:2000])


def open_url(url):
    # PUT /json/new?url=... opens the tab in the real Chrome session
    out = _agent("/exec", {"cmd": f'curl -s -X PUT "http://localhost:9222/json/new?{urllib.parse.quote(url, safe="")}"'})
    print(out.get("out", "")[:400])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        status()
    elif cmd == "launch":
        launch()
    elif cmd == "tabs":
        tabs()
    elif cmd == "open":
        open_url(sys.argv[2])
    else:
        print(__doc__)
