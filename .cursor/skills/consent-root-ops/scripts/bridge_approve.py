#!/usr/bin/env python3
"""bridge_approve.py — atomic X OAuth approve on the dedicated adhoc
browser (browser-novnc-adhoc :9232, no shared-session contention).

Single-connection sequence: session/start → seed auth_token+ct0 →
establish session on x.com → nav authorize URL → dismiss cookie modal →
click Authorize. The whole flow runs before the session's idle timeout.

    bridge_approve.py <oauth_url>
"""
import json
import re
import sys
import time
import urllib.request

BRIDGE = "http://localhost:9232"


def _call(path: str, body: dict | None = None, t: float = 25.0) -> dict:
    data = json.dumps(body or {}).encode() if body is not None else None
    r = urllib.request.Request(f"{BRIDGE}{path}", data=data,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=t) as f:
            return json.loads(f.read() or b"{}")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _ev(js: str) -> str:
    r = _call("/session/evaluate", {"expression": js})
    return str(r.get("result") or r.get("error") or "?")[:220]


def approve(auth_url: str, env_path: str = ".env") -> str:
    env = open(env_path).read()
    auth = re.search(r'X_WEB_AUTH_TOKEN=(\S+)', env).group(1)
    ct0 = re.search(r'X_WEB_CT0=(\S+)', env).group(1)

    # 1. dedicated session slot — no 409 contention
    r = _call("/session/start", {"platform": "twitter"})
    if "waiting" not in str(r) and "ok" not in str(r):
        return f"session-start: {str(r)[:120]}"

    # 2. seed x session cookies
    seed = _ev(
        f"document.cookie='auth_token={auth};domain=.x.com;path=/;Secure';"
        f"document.cookie='ct0={ct0};domain=.x.com;path=/;Secure';'ok'")
    if "ok" not in seed:
        return f"seed: {seed}"

    # 3. establish the session on x.com (cookies bind to the domain)
    _ev("location.href='https://x.com/home'")
    time.sleep(6)
    page = _ev("document.title")
    if "home" not in page.lower() and "x" not in page.lower():
        return f"session-check: {page}"

    # 4. navigate the authorize URL
    _ev(f"location.href={json.dumps(auth_url)}")
    time.sleep(6)

    # 5. dismiss cookie-consent modal if present
    _ev("(()=>{const b=[...document.querySelectorAll('button')]"
        ".find(e=>/accept all cookies/i.test(e.innerText||''));"
        "b&&b.click();return b?'ok':'none'})()")
    time.sleep(2)

    # 6. click Authorize — poll for the button across renders
    for _ in range(8):
        clicked = _ev(
            "(()=>{const b=[...document.querySelectorAll('button,input')]"
            ".find(e=>/authorize app/i.test(e.innerText||e.value||''));"
            "b&&b.click();return b?'clicked':'no-btn'})()")
        if "clicked" in clicked:
            time.sleep(8)
            land = _ev("location.href")
            if "oauth2/authorize" not in land:
                return f"approved → {land[:120]}"
        time.sleep(2)
    return f"approve-stall {_ev('location.href')[:100]}"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: bridge_approve.py <oauth_url> [env_path]")
        return 2
    print(approve(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ".env"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
