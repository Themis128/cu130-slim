#!/usr/bin/env python3
"""cdp_cookie_copy — transplant login cookies between live CDP browsers.

Reads cookies straight from the user's REAL browser over CDP (no file
locks, no DPAPI) and injects them into the debug chrome on :9333.

    python cdp_cookie_copy.py github.com cloudflare.com
    python cdp_cookie_copy.py --list            # domains with cookies on :9222
"""
import json
import sys
import urllib.request

SRC = "http://127.0.0.1:9222"   # real browser (Edge/Chrome, logged in)
DST = "http://127.0.0.1:9333"   # debug chrome


def _page(port):
    tabs = json.loads(urllib.request.urlopen(f"{port}/json", timeout=5).read())
    return next(t for t in tabs if t.get("type") == "page")


def _rpc(ws_url, calls):
    import websocket
    ws = websocket.create_connection(ws_url, timeout=15)
    out = []
    for i, (method, params) in enumerate(calls, 1):
        ws.send(json.dumps({"id": i, "method": method, "params": params}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == i:
                out.append(msg)
                break
    ws.close()
    return out


def _cookies(port, domains):
    page = _page(port)
    res = _rpc(page["webSocketDebuggerUrl"], [
        ("Storage.getCookies", {}),
    ])[0]
    cookies = res.get("result", {}).get("cookies", [])
    return [c for c in cookies
            if any(d.lstrip(".") in c.get("domain", "") for d in domains)]


def transplant(domains):
    src = _cookies(SRC, domains)
    if not src:
        print("no matching cookies on :9222 — is the real browser logged in?")
        return 1
    dst_page = _page(DST)
    ok = fail = 0
    for c in src:
        params = {
            "name": c["name"], "value": c["value"], "domain": c["domain"],
            "path": c.get("path", "/"), "secure": c.get("secure", False),
            "httpOnly": c.get("httpOnly", False),
        }
        if c.get("expires") and c["expires"] > 0:
            params["expires"] = c["expires"]
        res = _rpc(dst_page["webSocketDebuggerUrl"], [
            ("Network.setCookie", params)])[0]
        if res.get("result", {}).get("success"):
            ok += 1
        else:
            fail += 1
            print("fail:", c["domain"], c["name"], res.get("error"))
    print(f"transplanted {ok}, failed {fail} (of {len(src)} candidates)")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        cs = _cookies(SRC, [""])
        from collections import Counter
        for d, n in Counter(c["domain"] for c in cs).most_common(15):
            print(f"{n:4d}  {d}")
    elif len(sys.argv) > 1:
        sys.exit(transplant(sys.argv[1:]))
    else:
        print(__doc__)
