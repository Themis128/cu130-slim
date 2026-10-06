#!/usr/bin/env python3
"""Check the status of recent TikTok uploads by querying the SocialAuto API"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login, env, request, usage  # noqa: E402

api = api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")
admin_email = env("SOCIAL_ADMIN_EMAIL")
admin_pass = env("SOCIAL_ADMIN_PASSWORD")
TOKEN = api_login(api)
print("=== Recent TikTok post targets ===")
resp = request("GET", f"{api}/api/v1/content/posts?limit=30", token=TOKEN)
print(json.dumps(resp, indent=2, ensure_ascii=False))
print("")
print("=== Known upload_ids from worker logs ===")
print("No upload_ids found in logs.")
print(f"  upload_id={upid}  (publish_id=v_inbox_file~v2.{upid})")
print("")
print("To cancel: .devin/skills/tiktok-publish/scripts/cancel-upload.py v_inbox_file~v2.<upload_id>")
