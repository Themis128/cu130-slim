#!/usr/bin/env python3
"""Initialize the n8n API key after n8n starts.
Run this after the n8n container is up and running.
Usage: init-n8n-api-key.py"""

import base64
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
N8N_URL = "http://localhost:5678"
API_KEY_LABEL = "social-automation-api-key"
API_KEY_EXPIRY_DAYS = 365

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

auth = base64.b64encode(
    f"{env.get('N8N_USER', '')}:{env.get('N8N_PASSWORD', '')}"
    .encode()).decode()


def call(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{N8N_URL}{path}",
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Basic {auth}",
                 "Accept": "application/json",
                 "Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode() or "{}")


print("Waiting for n8n to be ready...")
while True:
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"{N8N_URL}/healthz",
            headers={"Authorization": f"Basic {auth}"}), timeout=5)
        break
    except Exception:
        print("  n8n not ready yet, waiting...")
        time.sleep(5)

print("n8n is ready!")

print("Checking for existing API key...")
try:
    existing = call("GET", "/api/v1/user/api-keys")
except Exception:
    existing = {}
for key in existing.get("data", []):
    if key.get("label") == API_KEY_LABEL:
        print(f"API key '{API_KEY_LABEL}' already exists")
        existing_key = key.get("key", "")
        if existing_key:
            print(f"Existing API key: {existing_key}")
            print(f"N8N_API_KEY={existing_key}")
            sys.exit(0)

print("Creating new API key...")
expires = (datetime.now(timezone.utc)
           + timedelta(days=API_KEY_EXPIRY_DAYS)).isoformat()
resp = call("POST", "/api/v1/user/api-keys", {
    "label": API_KEY_LABEL,
    "expiresAt": expires,
    "scopes": ["workflow:create", "workflow:read", "workflow:execute",
               "workflow:list", "workflow:update", "workflow:delete",
               "workflow:activate"],
})
print(f"Create response: {resp}")

api_key = (resp.get("data", {}).get("key") or resp.get("key")
           or resp.get("apiKey") or "")
if not api_key:
    print("ERROR: Failed to create API key", file=sys.stderr)
    print(f"Response: {resp}", file=sys.stderr)
    sys.exit(1)

print(f"Successfully created API key: {api_key}\n")
print("Add this to your .env file:")
print(f"N8N_API_KEY={api_key}\n")
print("Then restart social-api and social-worker containers:")
print("  docker compose restart social-api social-worker")

# Optionally update .env file automatically
text = env_file.read_text()
new_text, n = re.subn(r"^# N8N_API_KEY=.*", f"N8N_API_KEY={api_key}",
                      text, flags=re.MULTILINE)
if n == 0 and "N8N_API_KEY=" not in text:
    new_text = text.rstrip("\n") + f"\nN8N_API_KEY={api_key}\n"
if new_text != text:
    env_file.write_text(new_text)
    print("\n.env updated. Restart containers to apply.")
