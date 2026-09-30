#!/usr/bin/env python3
"""Trigger OAuth token refresh for all eligible connected accounts.
Usage: refresh-tokens.py"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login, request  # noqa: E402

api = api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")
token = api_login(api)
accounts = request("GET", f"{api}/api/v1/accounts", token=token)
items = accounts if isinstance(accounts, list) else accounts.get("accounts", [])
for a in items:
    acc_id = a.get("id")
    try:
        resp = request("POST", f"{api}/api/v1/accounts/{acc_id}/refresh", token=token)
        print(
            f"  {a.get('platform', '?'):12s} {a.get('display_name', '?')[:25]:25s} → {json.dumps(resp)[:80]}"
        )
    except SystemExit:
        print(
            f"  {a.get('platform', '?'):12s} {a.get('display_name', '?')[:25]:25s} → refresh failed"
        )
