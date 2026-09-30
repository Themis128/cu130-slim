#!/usr/bin/env python3
"""List brand assets."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login, request, usage  # noqa: E402

api = api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")
TOKEN = api_login(api)
resp = request("GET", f"{api}/api/v1/brand/assets", token=TOKEN)
print(json.dumps(resp, indent=2, ensure_ascii=False))
