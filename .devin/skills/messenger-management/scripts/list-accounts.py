#!/usr/bin/env python3
"""List all Messenger-capable accounts (Pages + personal)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

api, token = social_api()
data = request("GET", f"{api}/api/v1/accounts", token=token)
accounts = data.get("data", data) if isinstance(data, dict) else data
print("╔══════════════════════════════════════════════════════════╗")
print("║              Messenger-Capable Accounts                    ║")
print("╠══════════════════════════════════════════════════════════╣")
for a in accounts:
    if not isinstance(a, dict) or a.get("platform") != "facebook":
        continue
    name = a.get("display_name") or a.get("username", "Unknown")
    atype = a.get("account_type", "unknown")
    meta = a.get("meta_data") or {}
    if atype == "page":
        ms = meta.get("messenger_setup", {})
        sub = "✓ subscribed" if ms.get("subscribed") else "✗ not set up"
        print(f"║  📄 {name:<40} ║")
        print(f"║    Type: Page | {sub:<34} ║")
        print(f'║    ID: {a.get("id", ""):<44} ║')
    elif atype == "user":
        browser = "✓ logged in" if meta.get("browser_storage_state") else "✗ needs login"
        print(f"║  👤 {name:<40} ║")
        print(f"║    Type: Personal | {browser:<31} ║")
        print(f'║    ID: {a.get("id", ""):<44} ║')
    print("╠══════════════════════════════════════════════════════════╣")
print("║ Use the Page ID for Messenger Platform API endpoints     ║")
print("║ Use the Personal ID for browser bridge endpoints         ║")
print("╚══════════════════════════════════════════════════════════╝")
