#!/usr/bin/env python3
"""Schedule a post for future publishing.
Usage: schedule-post.py <post-id> "2026-09-01T10:00:00Z" """

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

if len(sys.argv) < 3:
    usage('schedule-post.py <post-id> "2026-09-01T10:00:00Z"')
post_id, scheduled_at = sys.argv[1], sys.argv[2]
api, token = social_api()
print(f"Scheduling post {post_id} for {scheduled_at}...")
d = request("POST",
            f"{api}/api/v1/content/posts/{post_id}/schedule"
            f"?scheduled_at={urllib.parse.quote(scheduled_at)}",
            token=token)
print(f'Status: {d.get("status", "?")}')
print(f'Scheduled at: {d.get("scheduled_at", "-")}')
