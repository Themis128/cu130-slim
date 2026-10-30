#!/usr/bin/env python3
"""root_approve.py — drive the user's REAL Chrome session via CDP.

The consent-gate root fix: X's OAuth approve needs a logged-in session —
the user's own Chrome on Windows already IS that session. Launch Chrome
with --remote-debugging-port=9222 on the Windows host (one-time via
start-chrome-debug.bat), then this script drives it to the auth URL and
clicks Authorize — no manual step, no cookie-seeding needed.

    root_approve.py <oauth_url>     # navigate + approve in the user's Chrome
"""
import json
import subprocess
import sys
import urllib.request

CDP = "http://localhost:9222"


def _ws(url: str) -> object:
    import websocket  # websocket-client — pip install
    return websocket.create_connection(url, timeout=15)


def _send(ws, method: str, params: dict | None = None) -> dict:
    ws.send(json.dumps({"id": 1, "method": method,
                        "params": params or {}}))
    return json.loads(ws.recv())


def tabs() -> list[dict]:
    with urllib.request.urlopen(f"{CDP}/json", timeout=8) as f:
        return [t for t in json.loads(f.read()) if t.get("type") == "page"]


def approve(auth_url: str) -> str:
    ts = tabs()
    if not ts:
        return "no-tab — is Chrome running with --remote-debugging-port=9222?"
    ws = _ws(ts[0]["webSocketDebuggerUrl"])
    _send(ws, "Page.enable")
    _send(ws, "Runtime.enable")
    _send(ws, "Page.navigate", {"url": auth_url})
    import time
    time.sleep(4)
    # click Authorize
    js = """
      (() => {
        const b = [...document.querySelectorAll('input[type=submit],button,a')]
          .find(e => /authoriz|approve|allow/i.test(e.innerText || e.value || ''));
        if (b) { b.click(); return 'clicked ' + (e=>e.innerText||e.value)(b); }
        return 'no-authorize-button — page: ' + document.title;
      })()
    """
    r = _send(ws, "Runtime.evaluate",
              {"expression": js, "returnByValue": True})
    ws.close()
    return r.get("result", {}).get("result", {}).get("value", "eval-fail")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: root_approve.py <oauth_url>")
        return 2
    print(approve(sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
