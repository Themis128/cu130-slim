#!/usr/bin/env python3
"""Validate a specific Instagram account's token via the Graph API.
Usage: validate-token.py <account_id>"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root, request, social_api, usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else usage("validate-token.py <account_id>")
api, token = social_api()

print(f"=== Validating Instagram token for account {account_id} ===", file=sys.stderr)

subprocess.run(
    ["docker", "compose", "exec", "-T", "social-worker-default",
     "celery", "-A", "app.worker.celery_app", "call",
     "app.worker.tasks.instagram_token_refresh.refresh_instagram_tokens"],
    cwd=repo_root())
time.sleep(10)

accounts = request("GET", f"{api}/api/v1/accounts", token=token)
accs = accounts if isinstance(accounts, list) else accounts.get("accounts", accounts.get("data", []))
for a in accs:
    if a.get("platform") == "instagram" and str(a["id"]).startswith(account_id[:8]):
        meta = a.get("meta_data", {}) or {}
        status = meta.get("instagram_token_status", "unknown")
        print(f"Status: {status}")
        if meta.get("instagram_token_error"):
            print(f'Error: {meta["instagram_token_error"][:200]}')
        if meta.get("instagram_token_checked_at"):
            print(f'Checked: {meta["instagram_token_checked_at"][:19]}')
        if status == "valid":
            print("✅ Token is valid")
        elif status == "refreshed":
            print("✅ Token was refreshed successfully")
        else:
            print("❌ Token needs reconnection — use reconnect-oauth.py")
        break
