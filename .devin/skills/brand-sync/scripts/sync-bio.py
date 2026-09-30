#!/usr/bin/env python3
"""Sync brand bio to all connected platforms.
Usage: sync-bio.py"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
api, token = social_api()

accounts = request("GET", f"{api}/api/v1/accounts", token=token)
accs = accounts if isinstance(accounts, list) else \
    accounts.get("accounts", accounts.get("data", []))
platforms = list(dict.fromkeys(a["platform"] for a in accs))

print("=== Syncing brand bio to all platforms ===\n")
for platform in platforms:
    print(f"--- {platform} ---")
    subprocess.run([sys.executable, str(SCRIPT_DIR / "sync-platform.py"),
                    platform])
    print()

print("=== All platforms processed ===")
