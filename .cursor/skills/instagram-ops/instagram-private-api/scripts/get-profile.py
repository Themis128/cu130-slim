#!/usr/bin/env python3
"""Get the current Instagram account profile via aiograpi-rest.
Usage: get-profile.py <session_id>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, request, usage  # noqa: E402

session_id = sys.argv[1] if len(sys.argv) > 1 else usage("get-profile.py <session_id>")
sidecar = api_base("INSTAGRAM_PRIVATE_API_URL", "http://localhost:8011")

print("Fetching Instagram profile...")
d = request("GET", f"{sidecar}/account", headers={"X-Session-ID": session_id})
print(f'Username: {d.get("username", "?")}')
print(f'Full name: {d.get("full_name", "?")}')
print(f'Biography: {d.get("biography", "?")}')
print(f'External URL: {d.get("external_url", "?")}')
print(f'Followers: {d.get("follower_count", "?")}')
print(f'Following: {d.get("following_count", "?")}')
print(f'Posts: {d.get("media_count", "?")}')
print(f'Is private: {d.get("is_private", "?")}')
print(f'Is verified: {d.get("is_verified", "?")}')
