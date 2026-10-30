#!/usr/bin/env python3
"""bsky_apppw_gen — create a Bluesky app password via the user's logged-in
bsky.app session on the debug Chrome (CDP :9222).

Drives Settings → App Passwords → Add, reads the generated password, and
writes it to the Downloads drop file — the human-gate-capture watcher
wires `bsky <password>` automatically within its cycle.

Usage: python3 bsky_apppw_gen.py
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

CDP = "http://127.0.0.1:9222"
DROP = Path("/mnt/c/Users/tbaltzakis/Downloads/bsky.txt")

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
    _ev(w, 'location.href="https://bsky.app/settings/app-passwords"'); time.sleep(6)
    logged = _ev(w, '!!document.querySelector("[aria-label*=profile i], [data-testid=profileTab]")')
    if not logged:
        sys.exit("bsky.app session not logged in — open it once on the debug Chrome")
    # click the add-app-password button (data-testid or text match)
    _ev(w, '''(() => { const b=[...document.querySelectorAll("button")].find(b=>/add app password|create/i.test(b.textContent)); b&&b.click(); return !!b })()''')
    time.sleep(2)
    _ev(w, '''(() => { const i=document.querySelector('input[name="name"],input[placeholder*="name" i],input[type="text"]'); if(i){i.value="socialauto";i.dispatchEvent(new Event("input",{bubbles:true}));} const c=[...document.querySelectorAll("button")].find(b=>/confirm|create|done/i.test(b.textContent)); c&&c.click(); return true })()''')
    time.sleep(3)
    # the generated password renders in a modal — read candidate text
    pw = _ev(w, '''(() => { const el=[...document.querySelectorAll("p,span,div")].find(e=>/^[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}$/i.test((e.textContent||"").trim())); return el ? el.textContent.trim() : null })()''')
    if not pw:
        sys.exit("password element not found — modal layout may need a manual pass")
    DROP.write_text(pw + "\n")
    print(f"app password dropped → {DROP} ({pw[:6]}…) — watcher wires it")

if __name__ == "__main__":
    main()
