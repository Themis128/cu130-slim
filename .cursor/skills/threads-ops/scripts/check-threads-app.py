#!/usr/bin/env python3
"""Check Threads app status and connected accounts."""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import TEAM_ID, accounts, team_token  # noqa: E402
from skill_http import repo_root  # noqa: E402

env_path = repo_root() / ".env"
print("=== Threads env configuration ===")
for line in env_path.read_text().splitlines():
    if line.startswith(("THREADS_CLIENT_ID", "THREADS_CLIENT_SECRET",
                        "THREADS_REDIRECT_URI", "FACEBOOK_CLIENT_ID",
                        "FACEBOOK_CLIENT_SECRET", "FACEBOOK_REDIRECT_URI")):
        print(line.split("=", 1)[0] + "=****" if "SECRET" in line else line)

print(f"\n=== Logging in and switching to team {TEAM_ID} ===")
api, token = team_token()

print("=== Threads OAuth URL ===")
data = __import__("json").loads(
    __import__("urllib.request").request.urlopen(
        __import__("urllib.request").request.Request(
            f"{api}/api/v1/auth/oauth/threads/authorize?team_id={TEAM_ID}",
            headers={"Authorization": f"Bearer {token}"}), timeout=15
    ).read().decode())
url = data.get("authorization_url", "")
qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
print(f"URL (truncated): {url[:120]}...")
print(f'Client ID: {qs.get("client_id", [""])[0]}')
print(f'Redirect URI: {qs.get("redirect_uri", [""])[0]}')
print(f'Scopes: {qs.get("scope", [""])[0]}')

print("\n=== Connected Threads accounts ===")
threads = [a for a in accounts(token) if a.get("platform") == "threads"]
if not threads:
    print("  No Threads accounts connected")
for a in threads:
    print(f'  ID: {a.get("id")}')
    print(f'    Username: {a.get("username", "?")}')
    print(f'    Display: {a.get("display_name", "?")}')
    print(f'    Status: {a.get("status", "?")}')
    print(f'    Expires: {a.get("token_expires_at", "?")}')
    print(f'    Scopes: {a.get("scopes", [])}')

print("\n=== Browser-novnc status ===")
try:
    with urllib.request.urlopen("http://localhost:9223/health", timeout=10) as r:
        d = json.loads(r.read().decode())
        print(f'Status: {d.get("status")}, URL: {d.get("novnc_url")}')
except Exception:
    print("  (bridge not available)")
