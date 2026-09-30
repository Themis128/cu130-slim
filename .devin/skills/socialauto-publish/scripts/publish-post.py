#!/usr/bin/env python3
"""Publish a draft post immediately via SocialAuto API.
Usage: publish-post.py <post-id>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

post_id = sys.argv[1] if len(sys.argv) > 1 else usage("publish-post.py <post-id>")
api, token = social_api()
print(f"Publishing post {post_id}...")
d = request("POST", f"{api}/api/v1/content/posts/{post_id}/publish-now", token=token)
print(f'Status: {d.get("status", "?")}')
print(f'Scheduled at: {d.get("scheduled_at", "-")}')
for t in d.get("targets", []):
    print(f'  Target: {t.get("platform", "?")} → {t.get("status", "?")}  '
          f'url={t.get("platform_url", "-")}')
