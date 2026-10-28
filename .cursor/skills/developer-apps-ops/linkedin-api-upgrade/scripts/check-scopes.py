#!/usr/bin/env python3
"""Check which LinkedIn scopes are currently configured and which accounts
are connected.
Usage: check-scopes.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root, request, social_api  # noqa: E402

print("=== LinkedIn API Scope Configuration ===\n")
print("--- Requested OAuth Scopes ---")
auth_py = repo_root() / "social-automation/backend/app/api/auth.py"
lines = auth_py.read_text().splitlines()
for i, line in enumerate(lines):
    if "LINKEDIN_SCOPES" in line:
        print("\n".join(lines[i:i + 9]))
        break
print()

print("--- Connected LinkedIn Accounts ---")
api, token = social_api()
accounts = request("GET", f"{api}/api/v1/accounts", token=token)
li = [a for a in accounts if a.get("platform") == "linkedin"]
for a in li:
    print(f'  id={a["id"]}  type={a.get("account_type", "")}  '
          f'name={a.get("display_name", "")}  status={a.get("status", "")}')
    print(f'    scopes: {a.get("scopes", [])}')
print(f"\nTotal LinkedIn accounts: {len(li)}")
print()
print("Note: Development tier allows max 5 members/pages/ad accounts.")
print("If you have more than 5 LinkedIn accounts, you need the standard tier upgrade.")
