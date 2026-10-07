#!/usr/bin/env python3
"""Initialize/rotate the n8n public API key (n8n 2.x).

n8n 2.x removed the 1.x `/api/v1/user/api-keys` minting endpoint — keys are
created via `/rest/api-keys` with a session cookie from `/rest/login`.

Usage: init-n8n-api-key.py

Reads N8N_USER / N8N_PASSWORD from .env (the owner login), mints a
`social-automation-api-key` with the full set of scopes the owner role can
grant (workflow + credential + execution + tag/variable read), writes it to
N8N_API_KEY in .env, and prints where to restart. The raw key is NEVER
printed.
"""

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
N8N_URL = "http://localhost:5678"
API_KEY_LABEL = "social-automation-api-key"

env_file = ROOT / ".env"
if not env_file.is_file():
    print(f"Error: .env file not found at {env_file}", file=sys.stderr)
    sys.exit(1)
env = {}
for line in env_file.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

USER = env.get("N8N_USER", "")
PASSWORD = env.get("N8N_PASSWORD", "")
if not USER or not PASSWORD:
    print("Error: N8N_USER / N8N_PASSWORD not set in .env", file=sys.stderr)
    sys.exit(1)


def post(path: str, body: dict, cookie: str | None = None) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(
        f"{N8N_URL}{path}", data=json.dumps(body).encode(),
        headers=headers, method="POST")
    return urllib.request.urlopen(req, timeout=15)


def get(path: str, cookie: str) -> dict:
    req = urllib.request.Request(
        f"{N8N_URL}{path}", headers={"Cookie": cookie,
                                     "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode() or "{}")


print("Logging in to n8n (session cookie)...")
try:
    with post("/rest/login",
              {"emailOrLdapLoginId": USER, "password": PASSWORD}) as r:
        cookies = r.headers.get_all("Set-Cookie", [])
except urllib.error.HTTPError as e:
    print(f"Login failed: HTTP {e.code} {e.read()[:200]}", file=sys.stderr)
    sys.exit(1)

cookie = next((c.split(";")[0] for c in cookies if c.startswith("n8n-auth=")),
              None)
if not cookie:
    print("Login failed: no n8n-auth cookie (MFA enabled?)", file=sys.stderr)
    sys.exit(1)
print("Logged in.")

print("Fetching grantable scopes for owner role...")
granted = set(get("/rest/api-keys/scopes", cookie).get("data", []))
want = [
    "workflow:read", "workflow:create", "workflow:update", "workflow:delete",
    "workflow:list", "workflow:activate", "workflow:deactivate",
    "workflow:export", "workflow:import", "workflow:move",
    "credential:read", "credential:create", "credential:update",
    "credential:delete", "credential:list", "credential:move",
    "execution:read", "execution:list", "execution:retry", "execution:stop",
    "execution:delete",
    "tag:read", "tag:list", "variable:list",
]
scopes = [s for s in want if s in granted]
skipped = [s for s in want if s not in granted]
if skipped:
    print(f"  (not grantable, skipped: {skipped})")

existing = get("/rest/api-keys", cookie).get("data", {})
items = existing.get("items", existing) if isinstance(existing, dict) \
    else existing
for key in items:
    if key.get("label") == API_KEY_LABEL:
        print(f"Deleting stale key '{API_KEY_LABEL}' ({key.get('id')})...")
        req = urllib.request.Request(
            f"{N8N_URL}/rest/api-keys/{key['id']}",
            headers={"Cookie": cookie}, method="DELETE")
        urllib.request.urlopen(req, timeout=15)

print(f"Minting '{API_KEY_LABEL}' with {len(scopes)} scopes...")
try:
    with post("/rest/api-keys",
              {"label": API_KEY_LABEL, "expiresAt": None,
               "scopes": scopes}, cookie=cookie) as r:
        item = json.loads(r.read().decode()).get("data", {})
except urllib.error.HTTPError as e:
    print(f"Mint failed: HTTP {e.code} {e.read()[:200]}", file=sys.stderr)
    sys.exit(1)

api_key = item.get("rawApiKey") or ""
if not api_key:
    print("ERROR: response had no rawApiKey", file=sys.stderr)
    sys.exit(1)

text = env_file.read_text()
new_text, n = re.subn(r"^#?\s*N8N_API_KEY=.*", f"N8N_API_KEY={api_key}",
                      text, flags=re.MULTILINE)
if n == 0:
    new_text = text.rstrip("\n") + f"\nN8N_API_KEY={api_key}\n"
env_file.write_text(new_text)

print("Done. N8N_API_KEY written to .env (value not printed).")
print("Recreate consumers: docker compose up -d --force-recreate "
      "social-api social-worker-default social-worker-publishing "
      "social-worker-media social-worker-messenger celery-beat")
