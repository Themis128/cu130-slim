#!/usr/bin/env python3
"""fb_session_seed.py — seed the fb-sidecar with the REAL business realm.

Meta's dev-console wall is an auth-realm gap: /data/fb-session.json holds
facebook.com posting cookies but not business.facebook.com /
developers.facebook.com. This tool launches the operator's REAL Chrome
with CDP, dumps the whole cookie jar via Network.getAllCookies (HttpOnly
included — no navigation, works even where FB blocks the page), converts
to Playwright storageState, and merges it into the sidecar session file.

Usage: python fb_session_seed.py            # real chrome → sidecar
       python fb_session_seed.py --check    # report realm coverage only
"""
import json
import subprocess
import sys
import time

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from gate_runner import _ensure_cdp, Page, tabs  # noqa: E402

SIDECAR = "facebook-browser-sidecar"
SESSION_FILE = "/data/fb-session.json"
REALMS = ("facebook.com", "business.facebook.com", "developers.facebook.com",
          "business.intern.facebook.com")


def cdp_cookies(_p=None):
    import json as _j
    import urllib.request
    import websocket
    ver = _j.load(urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=10))
    ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=20)
    try:
        ws.send(_j.dumps({"id": 1, "method": "Network.getAllCookies"}))
        while True:
            m = _j.loads(ws.recv())
            if m.get("id") == 1:
                return m.get("result", {}).get("cookies", [])
    finally:
        ws.close()
    return []


def to_storage_state(cookies):
    out = []
    for c in cookies:
        cc = {"name": c["name"], "value": c["value"], "domain": c["domain"],
              "path": c["path"], "expires": c.get("expires", -1),
              "httpOnly": c.get("httpOnly", False), "secure": c.get("secure", False),
              "sameSite": c.get("sameSite") or "Lax"}
        if cc["sameSite"] not in ("Strict", "Lax", "None"):
            cc["sameSite"] = "Lax"
        if cc["expires"] in (-1, None):
            cc["expires"] = -1
        out.append(cc)
    return {"cookies": out, "origins": []}


def realm_coverage(cookies):
    cov = {}
    for r in REALMS:
        cov[r] = sum(1 for c in cookies if r in c["domain"])
    return cov


def sidecar_get():
    r = subprocess.run(["docker", "compose", "exec", "-T", SIDECAR,
                        "cat", SESSION_FILE], capture_output=True, text=True,
                       cwd=r"C:\Users\tbaltzakis\cu130-slim")
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {"storageState": {"cookies": [], "origins": []}}


def sidecar_put(state):
    payload = json.dumps({"storageState": state, "cookies": state["cookies"], "savedAt": int(time.time() * 1000)})
    subprocess.run(["docker", "compose", "exec", "-T", SIDECAR,
                    "node", "-e", f"require('fs').writeFileSync('{SESSION_FILE}', process.argv[1])", payload],
                   capture_output=True, cwd=r"C:\Users\tbaltzakis\cu130-slim")


def main():
    if "--check" in sys.argv:
        cur = sidecar_get()["storageState"]
        print("sidecar realms:", realm_coverage(cur["cookies"]))
        return 0

    _ensure_cdp()
    cookies = cdp_cookies()
    cov = realm_coverage(cookies)
    print("real-chrome realms:", cov)
    keep = [c for c in cookies if any(r in c["domain"] for r in REALMS)]
    print(f"harvesting {len(keep)}/{len(cookies)} realm cookies")

    cur = sidecar_get()["storageState"]
    merged = { (c["domain"], c["name"]): c for c in cur["cookies"] }
    for c in to_storage_state(keep)["cookies"]:
        merged[(c["domain"], c["name"])] = c
    new_state = {"cookies": list(merged.values()), "origins": cur.get("origins", [])}
    sidecar_put(new_state)
    print("sidecar realms after merge:", realm_coverage(new_state["cookies"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
