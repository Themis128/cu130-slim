#!/usr/bin/env python3
"""Lightweight Messenger bot monitor — checks internal sidecar stats only.
Does NOT hit Meta APIs (no rate limit risk)."""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base  # noqa: E402

api = api_base("SOCIAL_API_URL", "http://localhost:8083")


def fetch(url: str):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, ValueError):
        return None


print("=== Messenger Bot Monitor ===")
print(time.strftime("%Y-%m-%d %H:%M:%S"))
print()

print("--- Sidecar Status ---")
d = fetch(f"{api}/api/v1/messenger/sidecar/status")
if d:
    stats, health = d.get("stats", {}), d.get("health", {})
    print(f'Status:          {d.get("status", "unknown")}')
    print(f'Health:          {health.get("status", "unknown")}')
    print(f'Events received: {stats.get("events_received", 0)}')
    print(f'Events processed:{stats.get("events_processed", 0)}')
    print(f'Auto-replies:    {stats.get("auto_replies_sent", 0)}')
    print(f'Errors:          {stats.get("errors", 0)}')
    print(f'Dedup cache:     {stats.get("dedup_cache_size", 0)}')
else:
    print("Sidecar unreachable")

print("\n--- Webhook Endpoint ---")
try:
    with urllib.request.urlopen(
        f"{api}/api/v1/messenger/webhook?hub.mode=subscribe"
        "&hub.verify_token=cloudless_messenger_verify&hub.challenge=monitor_test",
        timeout=10) as r:
        challenge = r.read().decode()
except urllib.error.URLError:
    challenge = "FAILED"
print("Verification: OK" if challenge.strip('" \n') == "monitor_test"
      else f"Verification: FAILED (got: {challenge})")

print("\n--- API Health ---")
d = fetch(f"{api}/health")
print(f'API: {d.get("status", "unknown")}' if d else "API unreachable")
print("\n=== Monitor complete ===")
