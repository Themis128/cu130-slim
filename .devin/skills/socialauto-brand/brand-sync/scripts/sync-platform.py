#!/usr/bin/env python3
"""Sync brand bio to a specific platform.
Usage: sync-platform.py <platform>"""

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env, request, social_api, usage  # noqa: E402

platform = sys.argv[1] if len(sys.argv) > 1 else usage("sync-platform.py <platform>")
api, token = social_api()
bridge = env("BROWSER_BRIDGE_URL", "http://localhost:9223")

# Generate bio from brand profile
bio = subprocess.run(
    [sys.executable, str(Path(__file__).resolve().parent / "generate-bio.py")],
    capture_output=True, text=True).stdout.strip()

print(f"=== Generated bio ===\n{bio}\n")

accounts = request("GET", f"{api}/api/v1/accounts", token=token)
accs = accounts if isinstance(accounts, list) else \
    accounts.get("accounts", accounts.get("data", []))
account = next((a for a in accs if a["platform"] == platform), None)
if not account:
    print(f"No {platform} account found.")
    sys.exit(1)
account_id = account["id"]

print(f"=== Updating {platform} (account: {account_id}) ===")


def bridge_eval(expression: str) -> None:
    req = urllib.request.Request(
        f"{bridge}/session/evaluate",
        data=json.dumps({"expression": expression}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception:
        pass


if platform == "threads":
    # Threads: use browser bridge
    print("Updating Threads bio via browser bridge...")
    username = account.get("username", "")

    req = urllib.request.Request(
        f"{bridge}/session/navigate",
        data=json.dumps({"url": f"https://www.threads.com/@{username}"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception:
        pass
    time.sleep(3)

    bridge_eval('(() => { const btns = document.querySelectorAll("div[role=button]"); '
                'for (const btn of btns) { if (btn.textContent.trim() === "Edit profile") '
                '{ btn.click(); return "clicked"; } } return "not found"; })()')
    time.sleep(2)

    bridge_eval('(() => { const dialog = document.querySelector("[role=dialog]"); '
                'const all = dialog.querySelectorAll("div[role=button]"); '
                'for (const el of all) { if (el.textContent.trim().startsWith("Bio")) '
                '{ el.click(); return "clicked"; } } return "not found"; })()')
    time.sleep(2)

    req = urllib.request.Request(
        f"{bridge}/session/fill",
        data=json.dumps({"selector": "textarea", "value": bio}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception:
        pass
    time.sleep(1)

    done_js = ('(function() { const all = Array.from(document.querySelectorAll('
               '"div[role=button], button")); const done = all.filter(b => '
               'b.innerText.trim() === "Done"); if (done.length > 0) '
               '{ done[done.length - 1].click(); return "clicked"; } '
               'return "no Done"; })()')
    bridge_eval(done_js)
    time.sleep(2)
    bridge_eval(done_js)
    time.sleep(3)

    print("Threads bio updated via browser bridge.")
else:
    # Other platforms: use SocialAuto profile API
    print(f"Updating {platform} bio via SocialAuto profile API...")
    try:
        result = request("PUT", f"{api}/api/v1/profile/{account_id}",
                         token=token, data={"bio": bio})
        print(json.dumps(result, indent=2))
    except SystemExit:
        print(f"Profile API update failed for {platform}")

print("\n=== Done ===")
