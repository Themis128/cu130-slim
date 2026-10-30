#!/usr/bin/env python3
"""viber_webadmin — extract the Viber bot app key from webadmin.viber.com
using the user's logged-in session on the debug Chrome (CDP :9222).

Finds the token on the bot info page, writes it to the Downloads drop —
human-gate-capture wires `viber <token>` in its cycle.

Usage: python3 viber_webadmin.py
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

CDP = "http://127.0.0.1:9222"
DROP = Path("/mnt/c/Users/tbaltzakis/Downloads/viber.txt")

def _page():
    tabs = json.loads(urllib.request.urlopen(f"{CDP}/json", timeout=5).read())
    return next((t for t in tabs if t.get("type") == "page"), None) or tabs[0]

def _ev(ws_url, expr):
    import websocket
    ws = websocket.create_connection(ws_url, timeout=20)
    ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr,"returnByValue":True,"awaitPromise":True}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == 1:
            ws.close(); return m.get("result", {}).get("result", {}).get("value")

def main():
    page = _page(); w = page["webSocketDebuggerUrl"]
    _ev(w, 'location.href="https://webadmin.viber.com/"'); time.sleep(6)
    url = _ev(w, "location.href") or ""
    if "login" in url or "auth" in url:
        sys.exit("webadmin.viber.com wants a login — sign in once on the debug Chrome")
    # token candidates: input values, code elements, td/div holding a 40+ char hex-ish string
    tok = _ev(w, '''(() => {
      const cand = [...document.querySelectorAll("input[value],code,.token,.app-key")]
        .map(e=>e.value||e.textContent).find(t=>t&&/^[0-9a-f]{30,}$/i.test(t.trim()));
      if (cand) return cand.trim();
      const el=[...document.querySelectorAll("*")].find(e=>e.children.length===0&&/^[0-9a-f]{30,}$/i.test((e.textContent||"").trim()));
      return el ? el.textContent.trim() : null;
    })()''')
    if not tok:
        sys.exit("no token candidate on the page — bot console may need one navigation click")
    DROP.write_text(tok + "\n")
    print(f"viber token dropped → {DROP} ({tok[:8]}…) — watcher wires it")

if __name__ == "__main__":
    main()
