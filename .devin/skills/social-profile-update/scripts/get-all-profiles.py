#!/usr/bin/env python3
"""Fetch current profile info for all connected social accounts.
Shows what fields are readable and their current values."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

api, token = social_api()

print("=== Connected Accounts ===")
accounts = request("GET", f"{api}/api/v1/accounts", token=token)
for a in (accounts if isinstance(accounts, list) else accounts.get("accounts", [])):
    print(f"  {a['platform']:12}  id={a['id']}  name={a.get('display_name','')}  "
          f"status={a.get('status','')}  type={a.get('account_type','')}")
print(f"\nTotal: {len(accounts)} accounts")
