#!/usr/bin/env python3
"""Get profile for a connected social account."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login, env, request, usage  # noqa: E402

api = api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")
admin_email = env("SOCIAL_ADMIN_EMAIL")
admin_pass = env("SOCIAL_ADMIN_PASSWORD")
account_id = sys.argv[1] if len(sys.argv) > 1 else ''
TOKEN = api_login(api)
resp = request("GET", f"{api}/api/v1/profile/{account_id}", token=TOKEN)
print(json.dumps(resp, indent=2, ensure_ascii=False))
