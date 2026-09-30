#!/usr/bin/env python3
"""Read Instagram profile via instagrapi sidecar.
Usage: ig-read-profile.py"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import IG_API, IG_SESSION_FILE, http  # noqa: E402
from skill_http import env  # noqa: E402

sid = env("IG_SESSION_ID")
if not sid and IG_SESSION_FILE.exists():
    sid = IG_SESSION_FILE.read_text().strip()

print("=== Instagram Profile ===")
if not sid:
    print("  (not logged in - run ig-login.py first)")
    sys.exit(0)

raw = http("GET", f"{IG_API}/account", headers={"X-Session-ID": sid})
try:
    d = json.loads(raw)
    for k in ("username", "full_name", "biography", "external_url",
              "follower_count", "following_count", "media_count"):
        print(f"  {k}: {d.get(k)}")
except ValueError:
    print(f"  (could not parse response: {raw[:200]})")
