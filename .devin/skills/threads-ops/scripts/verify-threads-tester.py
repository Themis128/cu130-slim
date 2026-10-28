#!/usr/bin/env python3
"""Verify the Threads app configuration and tester state."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

APP_ID = "1236823015256887"

env_path = repo_root() / ".env"
print("=== Threads .env configuration ===")
for line in env_path.read_text().splitlines():
    if line.startswith(("THREADS_CLIENT_ID", "THREADS_CLIENT_SECRET",
                        "THREADS_REDIRECT_URI")):
        print(line.split("=", 1)[0] + "=****" if "SECRET" in line else line)

print("\n=== Threads app validation ===")
subprocess.run(
    ["docker", "compose", "exec", "-T", "social-api", "python", "-c", f"""
import httpx, os
client_id = os.environ.get('THREADS_CLIENT_ID', '{APP_ID}')
secret = os.environ.get('THREADS_CLIENT_SECRET', '')
r = httpx.get(
    'https://graph.facebook.com/oauth/access_token',
    params={{'client_id': client_id, 'client_secret': secret, 'grant_type': 'client_credentials'}},
    timeout=30,
)
print(f'App token request: {{r.status_code}}')
if r.status_code == 200:
    token = r.json().get('access_token', '')
    r2 = httpx.get(f'https://graph.facebook.com/v21.0/{{client_id}}/permissions',
                   params={{'access_token': token}}, timeout=30)
    print(f'Permissions: {{r2.status_code}}')
    data = r2.json()
    if 'data' in data:
        for perm in data['data']:
            print(f'  {{perm.get("permission", "?")}}: {{perm.get("status", "?")}}')
    else:
        print(f'  {{data}}')
else:
    print(f'  {{r.json()}}')
"""], cwd=repo_root())

print("""
=== How to add a Threads tester ===
1. Go to https://developers.facebook.com/apps/1936126137016578/roles/roles/
2. Click 'Add People' -> 'Threads Tester'
3. Enter the Instagram/Threads username (e.g. cloudless_gr or cloudless.gr)
4. The user must accept the invite in Threads: Settings -> Account -> Website permissions

=== Then run ===
  python3 .devin/skills/threads-ops/scripts/switch-threads-account.py cloudless.gr
""")
