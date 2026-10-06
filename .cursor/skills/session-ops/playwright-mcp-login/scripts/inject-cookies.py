#!/usr/bin/env python3
"""Inject cookies into the browser-novnc container (port 9223).

Usage:
  inject-cookies.py <platform> <cookies-json-file>

Reads a JSON array of cookies and sets them in the browser-novnc browser
by navigating to the platform domain and using document.cookie for each
non-HTTP-only cookie. HTTP-only cookies require the Playwright context
directly (not available via the bridge API — use the /session/cookies
endpoint if the bridge supports it)."""

import json
import sys
import time
import urllib.request
from pathlib import Path

TARGETS = {
    "twitter": ("https://x.com", ".x.com"),
    "x": ("https://x.com", ".x.com"),
    "tiktok": ("https://www.tiktok.com", ".tiktok.com"),
    "threads": ("https://www.threads.com", ".threads.com"),
    "instagram": ("https://www.instagram.com", ".instagram.com"),
}

platform = sys.argv[1] if len(sys.argv) > 1 else ""
cookies_file = sys.argv[2] if len(sys.argv) > 2 else ""
if platform not in TARGETS or not cookies_file:
    print("Usage: inject-cookies.py <platform> <cookies-json-file>",
          file=sys.stderr)
    sys.exit(1)

url, domain = TARGETS[platform]
bridge = "http://localhost:9223"

print(f"=== Injecting cookies for {platform} ===", file=sys.stderr)
print(f"URL: {url}", file=sys.stderr)
print(f"Cookies file: {cookies_file}", file=sys.stderr)


def post(path: str, data: dict) -> dict:
    req = urllib.request.Request(
        f"{bridge}{path}", data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


print(f"Navigating browser-novnc to {url}...", file=sys.stderr)
post("/session/navigate", {"url": url})
time.sleep(3)

cookies = json.loads(Path(cookies_file).read_text())
for cookie in cookies:
    name = cookie.get("name", "")
    value = cookie.get("value", "")
    c_domain = cookie.get("domain", domain)
    path = cookie.get("path", "/")
    secure = cookie.get("secure", True)

    if cookie.get("httpOnly"):
        print(f"  SKIP (httpOnly): {name}", file=sys.stderr)
        continue

    cookie_str = f"{name}={value}; domain={c_domain}; path={path}"
    if secure:
        cookie_str += "; secure"
    cookie_str += "; max-age=86400"

    escaped = cookie_str.replace("'", "\\'")
    post("/session/evaluate", {"expression": f"document.cookie = '{escaped}'"})
    print(f"  SET: {name}={value[:20]}...", file=sys.stderr)

print("Cookie injection complete", file=sys.stderr)

print("Reloading page...", file=sys.stderr)
post("/session/navigate", {"url": url})
time.sleep(3)
print(f"Done. Check session with check-session.py {platform}",
      file=sys.stderr)
