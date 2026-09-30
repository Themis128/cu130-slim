#!/usr/bin/env python3
"""Delete a post by ID.
Usage: delete-post.py <post-id>"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login, request, usage  # noqa: E402

api = api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")
post_id = sys.argv[1] if len(sys.argv) > 1 else usage("delete-post.py <post-id>")
TOKEN = api_login(api)
print(f"Deleting post {post_id}...")
resp = request("DELETE", f"{api}/api/v1/content/posts/{post_id}", token=TOKEN)
print(json.dumps(resp, indent=2, ensure_ascii=False))
print("Deleted.")
