#!/usr/bin/env python3
"""Update Instagram biography via aiograpi-rest.
Usage: update-bio.py <session_id> "new bio text" """

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, request_status, usage  # noqa: E402

if len(sys.argv) < 3:
    usage('update-bio.py <session_id> "new bio"')
session_id, bio = sys.argv[1], sys.argv[2]
sidecar = api_base("INSTAGRAM_PRIVATE_API_URL", "http://localhost:8011")

print("Updating Instagram biography...")
code, resp = request_status("PATCH", f"{sidecar}/account/biography",
                            headers={"X-Session-ID": session_id},
                            data={"biography": bio})
print(f"Response: {resp}")
print("✓ Biography updated (if no error above)")
