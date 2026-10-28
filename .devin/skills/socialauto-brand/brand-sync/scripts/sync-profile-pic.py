#!/usr/bin/env python3
"""Upload logo as profile picture to all supported platforms.
Usage: sync-profile-pic.py [logo_path]"""

import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, upload  # noqa: E402

api, token = social_api()
logo = sys.argv[1] if len(sys.argv) > 1 else ""

# Get logo from brand profile if not provided
if not logo:
    brand = request("GET", f"{api}/api/v1/brand", token=token)
    logo_url = (brand.get("visual") or {}).get("logo_url", "")
    if "path=" in logo_url:
        path = logo_url.split("path=", 1)[1]
        try:
            req = urllib.request.Request(
                f"{api}/api/v1/media/view?path={path}",
                headers={"Authorization": f"Bearer {token}"})
            data = urllib.request.urlopen(req, timeout=30).read()
            Path("/tmp/brand-logo.png").write_bytes(data)
            logo = "/tmp/brand-logo.png"
        except Exception:
            pass

if not logo or not Path(logo).is_file():
    print("No logo found. Provide a path or set logo in brand profile.")
    sys.exit(1)

print(f"=== Using logo: {logo} ===")
subprocess.run(["file", logo])
print()

accounts = request("GET", f"{api}/api/v1/accounts", token=token)
accs = accounts if isinstance(accounts, list) else \
    accounts.get("accounts", accounts.get("data", []))

print("=== Uploading profile picture to all platforms ===\n")
for a in accs:
    platform, username = a["platform"], a.get("username", "")
    print(f"--- {platform} ({username}) ---")
    if platform in ("instagram", "facebook", "linkedin", "twitter"):
        try:
            d = upload("POST", f"{api}/api/v1/profile/{a['id']}/picture",
                       logo, field="file", token=token)
            if d.get("success"):
                print(f'  OK: {d.get("updated_fields", [])}')
            else:
                print(f'  Failed: {d.get("detail", d.get("message", "unknown"))}')
        except SystemExit:
            print(f"  Failed: {platform} does not support picture upload via API")
    elif platform == "threads":
        print("  Skipped: Threads profile pic synced from Instagram")
    elif platform == "tiktok":
        print("  Skipped: TikTok does not support picture upload via API")
    else:
        print(f"  Skipped: {platform} not supported")
    print()

print("=== Done ===")
