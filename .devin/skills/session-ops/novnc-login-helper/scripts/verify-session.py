#!/usr/bin/env python3
"""Verify a platform session is active in the browser-novnc container
and extract/persist cookies for cross-restart survival.

Usage:
  verify-session.py <platform>   (twitter, threads, tiktok, instagram)"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import CHECK_URLS, check_logged_in, post  # noqa: E402

platform = sys.argv[1] if len(sys.argv) > 1 else ""
if platform not in CHECK_URLS:
    print("Usage: verify-session.py <platform>", file=sys.stderr)
    print("Platforms: twitter, threads, tiktok, instagram", file=sys.stderr)
    sys.exit(1)

# Prefer the check-session script from playwright-mcp-login skill
check_py = (Path(__file__).resolve().parent.parent.parent
            / "playwright-mcp-login/scripts/check-session.py")
if check_py.is_file():
    r = subprocess.run([sys.executable, str(check_py), platform],
                       capture_output=True)
    if r.returncode == 0:
        print(f"✅ Session verified for {platform}", file=sys.stderr)
    else:
        print(f"❌ Session NOT active for {platform}", file=sys.stderr)
        sys.exit(1)
else:
    post("/session/navigate", {"url": CHECK_URLS[platform]})
    time.sleep(4)
    if not check_logged_in(platform):
        print(f"❌ Session NOT active for {platform}", file=sys.stderr)
        sys.exit(1)

# Extract and persist cookies
print("Extracting cookies...", file=sys.stderr)
result = post("/session/extract", {"platform": platform})
cookies = result.get("cookies_found", result.get("cookies", "?"))
if isinstance(cookies, (dict, list)):
    cookies = len(cookies)
print(f"Cookies persisted: {cookies}", file=sys.stderr)
print(f"✅ {platform} session verified and cookies saved", file=sys.stderr)
