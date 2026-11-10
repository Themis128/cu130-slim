#!/usr/bin/env python3
"""bridge_sessions.py — check + seed platform sessions on the shared
browser bridge (browser-novnc:9223). Run inside social-api.

    bridge_sessions.py status            # per-platform login check
    bridge_sessions.py seed-x            # inject X auth_token/ct0 cookies
    bridge_sessions.py eval <js>         # raw /session/evaluate passthrough
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

BRIDGE = os.environ.get("BROWSER_BRIDGE_URL", "http://browser-novnc:9223")
PLATFORMS = {
    "x":        ("https://x.com/home", "compose|tweet|home"),
    "linkedin": ("https://www.linkedin.com/feed/", "feed|mynetwork|logout"),
    "tiktok":   ("https://www.tiktok.com/", "upload|profile|following"),
    "threads":  ("https://www.threads.net/", "home|search|activity"),
    "skool":    ("https://www.skool.com/sofia-kakkava-coaching", "write something|community"),
}




def pin() -> None:
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                "http://stack-ops:8787/keepawake/browser-novnc?ttl=600",
                method="POST", data=b""), timeout=8)
    except Exception:
        pass

def ev(expr: str, tries: int = 3) -> dict:
    last = None
    for _ in range(tries):
        r = urllib.request.Request(
            BRIDGE + "/session/evaluate",
            data=json.dumps({"expression": expr}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=20) as f:
                return json.loads(f.read())
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 409:
                time.sleep(20)
                continue
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}
    return {"error": f"busy after {tries} tries: {last}"}


def _res_obj(r: dict) -> dict:
    res = r.get("result") or {}
    if isinstance(res, str):
        try:
            res = json.loads(res)
        except ValueError:
            res = {"t": res}
    return res if isinstance(res, dict) else {}


def _bridge(method: str, path: str, data: dict | None = None):
    r = urllib.request.Request(
        BRIDGE + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as f:
            return json.loads(f.read())
    except Exception as e:
        return {"error": str(e)}


def _ensure_session() -> None:
    """status() needs a live browser context — start one if idle/dead."""
    st = _bridge("GET", "/session/status")
    if st.get("error") or st.get("status") in (None, "idle", "error", "stopped"):
        _bridge("POST", "/session/stop", {})
        _bridge("POST", "/session/start", {"platform": "twitter"})
        time.sleep(5)


def status() -> int:
    import urllib.parse
    _ensure_session()
    for name, (url, mark) in PLATFORMS.items():
        want_host = urllib.parse.urlparse(url).netloc.replace("www.", "")
        _bridge("POST", "/session/navigate", {"url": url})
        # Poll until the page actually lands on the target host — a fixed
        # sleep races slow navigations and reads the previous platform's DOM.
        for _ in range(10):
            time.sleep(3)
            rr = ev("location.href")
            cur_url = rr.get("result")
            if isinstance(cur_url, str) and want_host in cur_url:
                break
        r = ev("""
        (()=>({u:location.href,t:(document.body&&document.body.innerText||'').slice(0,2500)}))()
        """)
        res = _res_obj(r)
        body = str(res.get("t") or "")
        cur = str(res.get("u") or "")
        # A login/onboarding URL is a hard wall regardless of page text —
        # x.com/i/jf/onboarding and /i/flow/login both contain marker words.
        walled = any(w in cur.lower() for w in ("login", "signin", "sign-in", "i/flow", "onboarding", "auth"))
        logged = (not walled) and any(m in body.lower() for m in mark.split("|"))
        print(f"{name:10} {'logged_in' if logged else 'LOGIN_WALL'}  {cur[:70]}")
    return 0


def seed_x() -> int:
    tok = os.environ.get("X_WEB_AUTH_TOKEN", "")
    ct0 = os.environ.get("X_WEB_CT0", "")
    if not (tok and ct0):
        return err("need X_WEB_AUTH_TOKEN + X_WEB_CT0 in env")
    # ct0 is not HttpOnly — settable via document.cookie; auth_token IS
    # HttpOnly so JS can't set it — try the bridge cookie endpoint first.
    r = urllib.request.Request(
        BRIDGE + "/session/cookies",
        data=json.dumps({"cookies": [
            {"name": "auth_token", "value": tok, "domain": ".x.com", "path": "/", "secure": True, "httpOnly": True},
            {"name": "ct0", "value": ct0, "domain": ".x.com", "path": "/", "secure": True},
        ]}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=15) as f:
            print("cookies:", f.read()[:200])
            return 0
    except Exception as e:
        print("cookie endpoint failed:", str(e)[:120])
        # fallback — ct0 via document.cookie (auth_token still needs HttpOnly)
        print(ev(f"document.cookie='ct0={ct0}; domain=.x.com; path=/; secure'"))
        return 1


def err(msg: str) -> int:
    print(msg)
    return 1


def main() -> int:
    pin()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        return status()
    if cmd == "seed-x":
        return seed_x()
    if cmd == "eval":
        print(ev(" ".join(sys.argv[2:])))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
