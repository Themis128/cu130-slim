#!/usr/bin/env python3
"""wa_cookie_transplant.py — move the live FB session from the
facebook-browser-sidecar (:9226, Playwright context) into the debug
Chrome on CDP :9229, where wa_oauth_drive.py runs OAuth consents.

The sidecar keeps a warm logged-in session (has_session:true) that
survives Meta password changes differently than the debug chrome's —
transplanting its cookies (incl. HttpOnly xs/c_user/fr/datr) heals :9229
without any login form or 2FA.

  GET  localhost:9226/debug/all-cookies  →  CDP Network.setCookie × N
  verify: c_user present + facebook.com feed renders (no login wall)

Run:  python3 wa_cookie_transplant.py          # transplant + verify
      python3 wa_cookie_transplant.py --check  # report only
Never prints cookie values.
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

try:
    WSL_HOST_IP = os.environ.get("WSL_HOST_IP") or subprocess.run(
        ["sh", "-c", "ip route | awk '/default/ {print $3; exit}'"],
        capture_output=True, text=True, timeout=5).stdout.strip() \
        or "192.168.96.1"
except Exception:
    WSL_HOST_IP = "192.168.96.1"

CDP = "http://" + WSL_HOST_IP + ":9229"
SIDECAR = os.environ.get("FACEBOOK_SIDECAR", "http://localhost:9226")

# cookie name -> (domain, path, httpOnly, secure)
COOKIE_META = {
    "c_user": (".facebook.com", "/", True, True),
    "xs": (".facebook.com", "/", True, True),
    "fr": (".facebook.com", "/", True, True),
    "datr": (".facebook.com", "/", True, True),
    "sb": (".facebook.com", "/", False, True),
    "wd": (".facebook.com", "/", False, True),
    "dpr": (".facebook.com", "/", False, True),
    "locale": (".facebook.com", "/", False, True),
    "presence": (".facebook.com", "/", False, True),
}


def sidecar_cookies():
    d = json.loads(urllib.request.urlopen(
        SIDECAR + "/debug/all-cookies", timeout=20).read())
    return d.get("cookies", {})


def tabs():
    return json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=15).read())


def fb_tab():
    cands = [t for t in tabs() if t.get("type") == "page"
             and "facebook.com" in t.get("url", "")]
    if cands:
        return cands[0]
    req = urllib.request.Request(
        CDP + "/json/new?" + urllib.parse.quote("https://www.facebook.com/", safe=""),
        method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def cdp(ws_url, method, params=None, _id=1, timeout=20):
    from websockets.sync.client import connect
    with connect(ws_url, max_size=None) as ws:
        ws.send(json.dumps({"id": _id, "method": method,
                            "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            m = json.loads(ws.recv(timeout=timeout))
            if m.get("id") == _id:
                return m.get("result", {})
    return {}


def main():
    check_only = "--check" in sys.argv
    cookies = sidecar_cookies()
    if "c_user" not in cookies or "xs" not in cookies:
        print("FAIL: sidecar has no session cookies")
        sys.exit(2)
    print("sidecar cookies:", len(cookies),
          "| c_user present:", "c_user" in cookies)
    if check_only:
        sys.exit(0)

    t = fb_tab()
    ws = t["webSocketDebuggerUrl"]
    cdp(ws, "Network.enable")
    ok = fail = 0
    for name, value in cookies.items():
        dom, path, http_only, secure = COOKIE_META.get(
            name, (".facebook.com", "/", False, True))
        # values arrive URL-encoded from the dict export — keep as-is,
        # Meta stores them encoded server-side
        r = cdp(ws, "Network.setCookie", {
            "name": name, "value": str(value), "domain": dom,
            "path": path, "secure": secure, "httpOnly": http_only})
        if r.get("success"):
            ok += 1
        else:
            fail += 1
            print("  setCookie failed:", name)
    print(f"cookies set: {ok} ok / {fail} fail")

    # verify: /me must resolve to the profile (logged-out lands on / or
    # login). c_user is httpOnly — invisible to document.cookie, so the
    # redirect probe is the only reliable check.
    cdp(ws, "Page.navigate", {"url": "https://www.facebook.com/"})
    time.sleep(8)
    r = cdp(ws, "Runtime.evaluate", {"expression": """(async()=>{
      const resp=await fetch('https://www.facebook.com/me/',
        {credentials:'include',redirect:'follow'});
      return{url:resp.url};
    })()""", "returnByValue": True, "awaitPromise": True})
    me_url = (r.get("result", {}).get("value", {}) or {}).get("url", "")
    logged_in = "themis.baltzakis" in me_url or (
        "facebook.com" in me_url and "login" not in me_url
        and me_url.rstrip("/").endswith("facebook.com") is False)
    print("verify: /me ->", me_url[:80])
    if logged_in:
        print("DONE: :9229 FB session live")
        sys.exit(0)
    print("FAIL: session did not take — xs may be IP/device-bound")
    sys.exit(4)


if __name__ == "__main__":
    main()
