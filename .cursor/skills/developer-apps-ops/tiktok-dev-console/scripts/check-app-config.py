#!/usr/bin/env python3
"""Check TikTok app configuration from .env and verify the app is reachable.
Usage: check-app-config.py"""

import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env, repo_root  # noqa: E402

client_key = env("TIKTOK_CLIENT_KEY")
secret_set = bool(env("TIKTOK_CLIENT_SECRET"))
redirect_uri = env("TIKTOK_REDIRECT_URI")

print("=== TikTok App Configuration ===")
print(f'Client key:        {client_key or "<not set>"}')
print(f'Client secret:     {"<set>" if secret_set else "<not set>"}')
print(f'Redirect URI:      {redirect_uri or "<not set>"}')
print()
print("App ID:            7630494700880906241")
print("App name:          Cloudless")
print("Ownership:         Individual (needs transfer to organization)")
print("Target org:        cloudless.gr (7630331010873377809)")
print("Mode:              Sandbox (unaudited)")
print()

print("=== Connected TikTok Account ===")
list_script = repo_root() / ".devin/skills/socialauto-accounts/scripts/list-accounts.py"
r = subprocess.run([sys.executable, str(list_script)],
                   capture_output=True, text=True)
tiktok_lines = [ln for ln in r.stdout.splitlines() if "tiktok" in ln.lower()]
print("\n".join(tiktok_lines) if tiktok_lines else "No TikTok account connected")
print()

print("=== Redirect URI Reachability ===")
if redirect_uri:
    try:
        req = urllib.request.Request(redirect_uri, method="GET")
        urllib.request.urlopen(req, timeout=10)
        code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        code = 0
    print(f"Callback endpoint: {code} (expect 405 or 400 — endpoint exists but needs auth params)")
else:
    print("No TIKTOK_REDIRECT_URI set")
print()

print("=== Media URL Reachability (for PULL_FROM_URL) ===")
r = subprocess.run(
    ["docker", "compose", "exec", "-T", "social-api",
     "cat", "/run/tunnel/url"],
    cwd=repo_root(), capture_output=True, text=True)
tunnel_url = r.stdout.strip()
if tunnel_url:
    print(f"Tunnel URL: {tunnel_url}")
    try:
        urllib.request.urlopen(f"{tunnel_url}/health", timeout=10)
        code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        code = 0
    print(f"Health endpoint: {code}")
    print()
    print("Domain verification status: check in TikTok developer console")
    print("  → https://developers.tiktok.com → Cloudless app → URL properties")
else:
    print("No tunnel URL found — Cloudflare tunnel may not be running")
