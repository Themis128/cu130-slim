#!/usr/bin/env python3
"""Poll the browser-novnc bridge until the platform session is active.
Returns exit code 0 when logged in, 1 on timeout.

Usage:
  wait-for-login.py <platform> [timeout_seconds]"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import CHECK_URLS, check_logged_in, post  # noqa: E402

platform = sys.argv[1] if len(sys.argv) > 1 else ""
timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 300
if platform not in CHECK_URLS:
    print("Usage: wait-for-login.py <platform> [timeout_seconds]",
          file=sys.stderr)
    print("Platforms: twitter, threads, tiktok, instagram", file=sys.stderr)
    sys.exit(1)

url = CHECK_URLS[platform]
print(f"=== Waiting for {platform} login (timeout: {timeout}s) ===",
      file=sys.stderr)

post("/session/navigate", {"url": url})

elapsed, interval = 0, 5
while elapsed < timeout:
    time.sleep(interval)
    elapsed += interval

    if check_logged_in(platform):
        print(f"✅ {platform} login detected after {elapsed}s",
              file=sys.stderr)
        sys.exit(0)

    print(f"  [{elapsed}/{timeout}s] Not logged in yet...", file=sys.stderr)

    # Re-navigate every 30 seconds in case the page redirected
    if elapsed % 30 == 0:
        post("/session/navigate", {"url": url})

print(f"❌ Timeout waiting for {platform} login after {timeout}s",
      file=sys.stderr)
sys.exit(1)
