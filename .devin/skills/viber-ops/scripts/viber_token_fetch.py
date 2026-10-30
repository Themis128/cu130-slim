#!/usr/bin/env python3
"""viber_token_fetch.py — get the Viber Business bot token.

Path A (automated): drives the user's real Chrome via CDP :9222 to
webadmin.viber.com — if a session exists, opens the bot edit page and
pulls the token from the UI.

Path B (manual): waits for the user to paste the token, then validates
it against chatapi.viber.com (get_account_info) and stores
VIBER_AUTH_TOKEN in .env.

Usage:
  viber_token_fetch.py            # try CDP, fall back to manual paste
  viber_token_fetch.py --manual   # skip CDP, just validate+store
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "_lib")))

CDP = "http://localhost:9222"
ENV_PATH = r"C:\Users\tbaltzakis\cu130-slim\.env"
WEBADMIN = "https://partners.viber.com/account/edit-bot-account"


def try_cdp():
    try:
        from cdp_common import ensure_cdp
        import websocket
        ensure_cdp()
        tabs = json.load(urllib.request.urlopen(f"{CDP}/json", timeout=6))
        pages = [t for t in tabs if t.get("type") == "page"]
        if not pages:
            return None
        ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"],
                                         timeout=15, suppress_origin=True)
        i = [0]

        def send(m, p=None):
            i[0] += 1
            ws.send(json.dumps({"id": i[0], "method": m, "params": p or {}}))
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == i[0]:
                    return r

        send("Page.enable")
        send("Page.navigate", {"url": WEBADMIN})
        time.sleep(8)
        r = send("Runtime.evaluate", {
            "expression": "document.body.innerText.slice(0,1500)",
            "returnByValue": True})
        txt = r.get("result", {}).get("result", {}).get("value", "")
        m = re.search(r"[0-9a-f]{24}-[0-9a-f-]{27,}", txt)
        return m.group(0) if m else None
    except Exception:
        return None


def validate(token):
    req = urllib.request.Request(
        "https://chatapi.viber.com/pa/get_account_info",
        data=b"{}", headers={"X-Viber-Auth-Token": token,
                             "Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=15))
    return d.get("status") == 0, d


def store(token):
    line = f"VIBER_AUTH_TOKEN={token}\n"
    if os.path.exists(ENV_PATH):
        s = open(ENV_PATH, encoding="utf-8", errors="replace").read()
        s = re.sub(r"^VIBER_AUTH_TOKEN=.*", line.strip(), s, flags=re.M) \
            if "VIBER_AUTH_TOKEN=" in s else s + "\n" + line
        open(ENV_PATH, "w", encoding="utf-8").write(s)
    print("VIBER_AUTH_TOKEN stored")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manual", action="store_true")
    a = ap.parse_args()

    token = None if a.manual else try_cdp()
    if token:
        print("CDP token:", token[:12] + "…")
    else:
        print("no CDP session/token — paste the bot token from "
              "webadmin.viber.com (Bot -> your bot -> token):")
        token = input("token> ").strip()
    ok, info = validate(token)
    if ok:
        store(token)
        print("bot:", info.get("name"), "| subs:", info.get("subscribers_count"))
    else:
        print("invalid token:", info)


if __name__ == "__main__":
    main()
