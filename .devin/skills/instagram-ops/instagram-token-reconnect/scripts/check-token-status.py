#!/usr/bin/env python3
"""Check Instagram token status for all accounts."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root, request, social_api  # noqa: E402

api, token = social_api()

print("=== Instagram Token Status ===", file=sys.stderr)
accounts = request("GET", f"{api}/api/v1/accounts", token=token)
accs = accounts if isinstance(accounts, list) else accounts.get("accounts", accounts.get("data", []))
ig = [a for a in accs if a.get("platform") == "instagram"]
if not ig:
    print("No Instagram accounts found")
    sys.exit(0)
for a in ig:
    meta = a.get("meta_data", {}) or {}
    print()
    print(f'Account: {a.get("display_name", a.get("username", "?"))}')
    print(f'  ID: {a["id"][:8]}...')
    print(f'  Has token: {bool(a.get("access_token_enc"))}')
    print(f'  Token status: {meta.get("instagram_token_status", "unknown")}')
    if meta.get("instagram_token_error"):
        print(f'  Error: {meta["instagram_token_error"][:100]}')
    if meta.get("instagram_token_expires_at"):
        print(f'  Expires: {meta["instagram_token_expires_at"][:19]}')
    if meta.get("instagram_token_refreshed_at"):
        print(f'  Last refresh: {meta["instagram_token_refreshed_at"][:19]}')
    if meta.get("instagram_token_checked_at"):
        print(f'  Last check: {meta["instagram_token_checked_at"][:19]}')

print("\n=== Triggering token refresh task ===", file=sys.stderr)
subprocess.run(
    ["docker", "compose", "exec", "-T", "social-worker-default",
     "celery", "-A", "app.worker.celery_app", "call",
     "app.worker.tasks.instagram_token_refresh.refresh_instagram_tokens"],
    cwd=repo_root())
