#!/usr/bin/env python3
"""li_portal_dump.py — dump a LinkedIn developer-portal app's Auth/Products
pages as plain text via the user's logged-in browser over CDP.

LinkedIn's developer portal is an Ember SPA behind Cloudflare; API access is
unavailable. This opens a *background* tab on a CDP-enabled browser that is
already logged into LinkedIn, reads document.body.innerText, and closes the
tab. No focus stealing, no secrets printed (client SECRET is never expanded).

Requires: browser on :9222 with remote debugging and a live LinkedIn session
(the user's Edge debug instance / chrome-debug profile).

Usage:
  li_portal_dump.py 265077368                 # auth + products text
  li_portal_dump.py 265077368 --tab products  # products page only
  li_portal_dump.py --cdp http://localhost:9222 265077368 --json
"""
from __future__ import annotations
import argparse, json, sys, time, urllib.request


def cdp_ws():
    try:
        import websocket  # type: ignore
        return websocket
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "websocket-client", "-q"])
        import websocket  # type: ignore
        return websocket


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("app_id")
    ap.add_argument("--cdp", default="http://localhost:9222")
    ap.add_argument("--tab", choices=["auth", "products", "both"], default="both")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    websocket = cdp_ws()
    ver = json.loads(urllib.request.urlopen(
        f"{args.cdp}/json/version", timeout=10).read())
    bws = websocket.create_connection(ver["webSocketDebuggerUrl"],
                                      timeout=30, suppress_origin=True)

    def send(ws, i, method, params=None):
        ws.send(json.dumps({"id": i, "method": method,
                            "params": params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == i:
                return m.get("result", {})

    tabs = ["auth", "products"] if args.tab == "both" else [args.tab]
    out = {}
    for page in tabs:
        url = f"https://www.linkedin.com/developers/apps/{args.app_id}/{page}"
        t = send(bws, 1, "Target.createTarget",
                 {"url": url, "newWindow": False, "background": True})
        tid = t["targetId"]
        try:
            # wait for SPA to render, then read innerText via the page ws
            time.sleep(7)
            listing = json.loads(urllib.request.urlopen(
                f"{args.cdp}/json/list", timeout=10).read())
            entry = next((e for e in listing
                          if e.get("id") == tid), None)
            if not entry:
                out[page] = "error: tab gone"
                continue
            pws = websocket.create_connection(
                entry["webSocketDebuggerUrl"], timeout=30,
                suppress_origin=True)
            r = send(pws, 1, "Runtime.evaluate", {
                "expression": "document.body.innerText",
                "returnByValue": True})
            out[page] = r.get("result", {}).get("value", "")
            pws.close()
        finally:
            send(bws, 2, "Target.closeTarget", {"targetId": tid})

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        for page, text in out.items():
            print(f"===== {args.app_id} /{page} =====")
            print(text)


if __name__ == "__main__":
    main()
