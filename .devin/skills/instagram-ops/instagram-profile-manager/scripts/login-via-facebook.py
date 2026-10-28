#!/usr/bin/env python3
"""Login to Instagram via Facebook browser sidecar and extract sessionid.

The Facebook sidecar has a logged-in FB session. We navigate to Instagram's
"Log in with Facebook" flow, which uses the FB OIDC SSO. After landing on
the Instagram feed, we extract the httpOnly sessionid cookie via
Playwright's context.cookies() API (the /debug/all-cookies endpoint).

Usage: login-via-facebook.py [ig_username]
  ig_username defaults to "cloudless.gr"

Output: writes the sessionid to /tmp/ig-sessionid.txt and prints it."""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base  # noqa: E402

FB = api_base("FB_SIDECAR_URL", "http://localhost:9226")
IG_USERNAME = sys.argv[1] if len(sys.argv) > 1 else "cloudless.gr"


def fb(method: str, path: str, data: dict | None = None) -> str:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(FB + path, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode()
    except urllib.error.URLError:
        return ""


def fb_json(method: str, path: str, data: dict | None = None) -> dict:
    try:
        return json.loads(fb(method, path, data))
    except ValueError:
        return {}


def page_text() -> str:
    return fb_json("GET", "/debug/page-text").get("text", "")


def evaluate(script: str) -> str:
    return fb_json("POST", "/debug/eval", {"script": script}).get("result", "")


print("=== Instagram Login via Facebook SSO ===")

status = fb_json("GET", "/session").get("logged_in")
if status is not True:
    print("ERROR: Facebook sidecar is not logged in.")
    sys.exit(1)
print("✓ Facebook sidecar is logged in")

print("→ Navigating to Instagram login...")
fb("POST", "/debug/navigate",
   {"url": "https://www.instagram.com/accounts/login/?force_classic_login=true"})
time.sleep(3)

text = page_text()
if IG_USERNAME in text:
    print(f"→ Found profile picker, clicking {IG_USERNAME}...")
    evaluate(
        "(async () => { const divs = document.querySelectorAll('div[role=button]'); "
        f"for (const d of divs) {{ if (d.textContent.trim() === '{IG_USERNAME}') "
        "{ d.click(); return 'clicked'; } } return 'not found'; })()")
    time.sleep(8)
elif "log in with facebook" in text.lower():
    print("→ Clicking 'Log in with Facebook'...")
    evaluate(
        '(async () => { const btns = document.querySelectorAll("button, div[role=button]"); '
        'for (const b of btns) { if (b.textContent.includes("Facebook")) '
        '{ b.click(); return "clicked"; } } return "not found"; })()')
    time.sleep(5)

    if "Continue" in page_text():
        print("→ On FB OIDC page, clicking Continue...")
        evaluate(
            '(async () => { const divs = document.querySelectorAll("div[role=button], button"); '
            'for (const d of divs) { if (d.textContent.trim() === "Continue") '
            '{ d.click(); await new Promise(r => setTimeout(r, 8000)); return document.URL; } } '
            'return "not found"; })()')
        time.sleep(5)

    if IG_USERNAME in page_text():
        print(f"→ Found profile picker, clicking {IG_USERNAME}...")
        evaluate(
            "(async () => { const divs = document.querySelectorAll('div[role=button]'); "
            f"for (const d of divs) {{ if (d.textContent.trim() === '{IG_USERNAME}') "
            "{ d.click(); await new Promise(r => setTimeout(r, 8000)); return document.URL; } } "
            "return 'not found'; })()")
        time.sleep(8)

current_url = evaluate("document.URL")
print(f"→ Current URL: {current_url}")
if "instagram.com" not in current_url or "login" in current_url:
    print(f"ERROR: Not logged in to Instagram. URL: {current_url}")
    sys.exit(1)

print("→ Extracting Instagram cookies...")
cookies = fb_json("GET", "/debug/all-cookies?domain=instagram.com").get("cookies", {})
sessionid = cookies.get("sessionid", "")
if not sessionid:
    print("ERROR: No sessionid cookie found. Available cookies:")
    for k in cookies:
        print(f"  {k}")
    sys.exit(1)

Path("/tmp/ig-sessionid.txt").write_text(sessionid)
print(f"✓ SessionID extracted: {sessionid[:30]}...")
print("  Saved to /tmp/ig-sessionid.txt")

print("\n→ Logging in to Instagram sidecar...")
body = json.dumps({"sessionid": sessionid}).encode()
req = urllib.request.Request("http://localhost:8011/auth/login/by/sessionid",
                             data=body, method="POST",
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode())
except Exception:
    d = {}

sid = d.get("session_id") or d.get("sessionid") or ""
user = d.get("user", {})
if sid:
    print("✓ Instagram sidecar login successful")
    print(f"  Sidecar Session ID: {sid[:30]}...")
    print(f"  Username: {user.get('username', '')}")
    print(f"  PK: {user.get('pk', '')}")
    Path("/tmp/ig-sidecar-session.txt").write_text(sid)
    print("  Saved sidecar session to /tmp/ig-sidecar-session.txt")
else:
    print("⚠ Sidecar login failed. SessionID may be expired.")
