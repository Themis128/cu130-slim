#!/usr/bin/env python3
"""Check if a platform session is active in the browser-novnc container.

Usage:
  check-session.py <platform>   (twitter, tiktok, threads, instagram)

Navigates to the platform and checks for login indicators."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                    / "novnc-login-helper/scripts"))
from _common import CHECK_URLS, check_logged_in, post  # noqa: E402

platform = sys.argv[1] if len(sys.argv) > 1 else ""
if platform not in CHECK_URLS:
    print("Usage: check-session.py <platform>", file=sys.stderr)
    print("Platforms: twitter, tiktok, threads, instagram", file=sys.stderr)
    sys.exit(1)

url = CHECK_URLS[platform]
print(f"=== Checking {platform} session in browser-novnc ===", file=sys.stderr)

print(f"Navigating to {url}...", file=sys.stderr)
post("/session/navigate", {"url": url})
time.sleep(4)

print("Checking login state...", file=sys.stderr)
result = check_logged_in(platform)
current_url = post("/session/evaluate",
                   {"expression": "window.location.href"}).get("result", "?")

print(f"\nCurrent URL: {current_url}", file=sys.stderr)
print(f"Logged in: {result}", file=sys.stderr)

if result:
    print(f"✅ Session active for {platform}", file=sys.stderr)
    sys.exit(0)
print(f"❌ No active session for {platform}", file=sys.stderr)
sys.exit(1)
