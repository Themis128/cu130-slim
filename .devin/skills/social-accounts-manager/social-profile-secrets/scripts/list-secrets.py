#!/usr/bin/env python3
"""List all stored social profile secrets (values masked).
Usage: list-secrets.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login, request  # noqa: E402

api = api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")
token = api_login(api)
resp = request("GET", f"{api}/api/v1/secrets", token=token)
items = resp if isinstance(resp, list) else resp.get("secrets", resp.get("items", []))
for s in items:
    key = s.get("key", s) if isinstance(s, dict) else s
    desc = s.get("description", "") if isinstance(s, dict) else ""
    print(f"  {key:40s} {desc}")
