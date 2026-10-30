#!/usr/bin/env python3
"""cdp_click.py <url> <text-regex> — click a consent/gate in the real Chrome.

Drives the user's actual logged-in session via CDP :9222 (debug profile —
start with start-chrome-debug.bat). Finds the first button/link/input whose
visible text or value matches the regex and clicks it.
"""
import json
import sys
import time
import urllib.request

CDP = "http://localhost:9222"


def _ws(url):
    import websocket
    return websocket.create_connection(url, timeout=15)


def _send(ws, mid, method, params=None):
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == mid:
            return m


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: cdp_click.py <url> <text-regex> [wait-s]")
        return 2
    url, pat = sys.argv[1], sys.argv[2]
    wait_s = float(sys.argv[3]) if len(sys.argv) > 3 else 5
    tabs = [t for t in json.loads(
        urllib.request.urlopen(f"{CDP}/json", timeout=8).read())
        if t.get("type") == "page"]
    if not tabs:
        print("no-tab — start the debug Chrome first")
        return 1
    ws = _ws(tabs[0]["webSocketDebuggerUrl"])
    _send(ws, 1, "Page.enable")
    _send(ws, 2, "Runtime.enable")
    _send(ws, 3, "Page.navigate", {"url": url})
    time.sleep(wait_s)
    js = f"""(() => {{
      const pat = /{pat}/i
      const c = [...document.querySelectorAll(
        'button,a,[role=button],[role=link],input[type=submit],input[type=button]')]
        .filter(e => !e.disabled && e.offsetParent !== null)
        .find(e => pat.test((e.innerText || e.value || e.getAttribute('aria-label') || '').slice(0, 100)))
      if (!c) return 'no-match — page: ' + document.title + ' @ ' + location.href;
      c.scrollIntoView({{block:'center'}})
 c.click()
      return 'clicked: ' + (c.innerText || c.value || c.tagName)
    }})()"""
    r = _send(ws, 4, "Runtime.evaluate", {"expression": js, "returnByValue": True})
    ws.close()
    print(r.get("result", {}).get("result", {}).get("value", "eval-fail"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
