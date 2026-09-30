#!/usr/bin/env python3
"""Upload a logo image and set it as the brand logo.
Usage: upload-logo.py <image_path>"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import die, request, social_api, upload, usage  # noqa: E402

image = sys.argv[1] if len(sys.argv) > 1 else usage("upload-logo.py <image_path>")
if not Path(image).is_file():
    die(f"File not found: {image}")
api, token = social_api()

print(f"=== Uploading {image} to media library ===")
d = upload("POST", f"{api}/api/v1/media/upload", image,
           extra={"alt_text": "Brand logo"}, token=token)
media_id = d.get("id", "")
if not media_id:
    die("Upload failed.")
print(f"Media ID: {media_id}")

print("\n=== Getting storage path ===")
storage_path = subprocess.run(
    ["docker", "compose", "exec", "-T", "social-postgres",
     "psql", "-U", "social_user", "-d", "social_automation", "-t", "-c",
     f"SELECT storage_path FROM media_assets WHERE id = '{media_id}';"],
    capture_output=True, text=True).stdout.strip()
print(f"Storage path: {storage_path}")
logo_url = f"/api/v1/media/view?path={storage_path}"

print("\n=== Setting logo URL in brand visual ===")
r = request("PUT", f"{api}/api/v1/brand/visual", token=token,
            data={"logo_url": logo_url})
print(f'Logo set to: {r.get("logo_url")}')

print("\n=== Adding as brand asset ===")
r = request("POST", f"{api}/api/v1/brand/assets", token=token,
            data={"name": "Brand Logo", "asset_type": "logo",
                  "media_id": media_id,
                  "description": "Brand logo uploaded via script"})
print(f'Asset added: {r.get("id")}')
print("\nDone. Logo uploaded and set.")
