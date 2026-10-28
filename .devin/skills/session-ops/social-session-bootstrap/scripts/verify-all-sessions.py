#!/usr/bin/env python3
"""Verify all social bot sessions and polling task health.
Usage: verify-all-sessions.py"""

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

api, token = social_api()

print("═══════════════════════════════════════════════")
print("  Social Session Bootstrap — Verification")
print("═══════════════════════════════════════════════\n")

# 1. API health
print("── API Health ──")
try:
    with urllib.request.urlopen(f"{api}/health", timeout=5) as r:
        print(f"  API: {r.status}")
except Exception as e:
    print(f"  API: unreachable ({e})")
print()

# 2. Container health
print("── Container Health ──")
for c in ("social-api", "social-worker-messenger", "social-worker-publishing",
          "social-worker-media", "social-worker-default", "celery-beat"):
    r = subprocess.run(
        ["docker", "inspect", "--format={{.State.Health.Status}}", c],
        capture_output=True, text=True)
    print(f"  {c}: {r.stdout.strip() if r.returncode == 0 else 'missing'}")
print()

# 3. Browser sessions
print("── Browser Sessions (browser-novnc) ──")


def bridge_post(path: str, data: dict) -> dict:
    req = urllib.request.Request(
        f"http://localhost:9223{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


URLS = {"twitter": "https://x.com/home",
        "tiktok": "https://www.tiktok.com/foryou",
        "threads": "https://www.threads.com/",
        "instagram": "https://www.instagram.com/"}
for platform, url in URLS.items():
    bridge_post("/session/navigate", {"url": url})
    time.sleep(2)
    current = bridge_post("/session/evaluate",
                          {"expression": "window.location.href"})
    print(f"  {platform}: {current.get('result', '?')}")
print()

# 4. Sidecar sessions
print("── Sidecar Sessions ──")
for port, name in ((9225, "linkedin"), (9226, "facebook")):
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/health",
                                    timeout=5) as r:
            health = r.status
    except Exception as e:
        health = f"unreachable ({e})"
    print(f"  {name} (port {port}): {health}")
print()

# 5. Celery task registration
print("── Celery Tasks ──")
r = subprocess.run(
    ["docker", "compose", "exec", "-T", "social-worker-default",
     "celery", "-A", "app.worker.celery_app", "inspect", "registered"],
    cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True)
if r.returncode == 0:
    text = r.stdout
    for t in ("poll_personal_messenger", "poll_instagram_messenger",
              "poll_threads_messenger", "poll_twitter_messenger",
              "poll_tiktok_messenger", "poll_linkedin_messenger",
              "refresh_instagram_tokens", "refresh_linkedin_sessions"):
        print(f"  {t}: {'✅' if t in text else '❌'}")
else:
    print("  Could not inspect tasks")
print()

# 6. Account token status
print("── Account Status ──")
try:
    accounts = request("GET", f"{api}/api/v1/accounts", token=token)
    accs = accounts if isinstance(accounts, list) else \
        accounts.get("accounts", accounts.get("data", []))
    for a in accs:
        platform = a.get("platform", "?")
        name = a.get("display_name", a.get("username", "?"))
        meta = a.get("meta_data", {}) or {}
        has_token = bool(a.get("access_token_enc"))
        bot = meta.get("bot_config") or meta.get("auto_reply")
        bot_enabled = (bot or {}).get("enabled", False) if bot else False
        print(f"  {platform:12s} | {name:20s} | token={has_token} | bot={bot_enabled}")
except SystemExit:
    print("  Could not fetch accounts")

print("\n═══════════════════════════════════════════════")
print("  Verification complete")
print("═══════════════════════════════════════════════")
