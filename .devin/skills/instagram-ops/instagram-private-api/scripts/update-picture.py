#!/usr/bin/env python3
"""Update Instagram profile picture via aiograpi-rest.
Usage: update-picture.py <session_id> <image_file>"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, die, usage  # noqa: E402

if len(sys.argv) < 3:
    usage("update-picture.py <session_id> <image_file>")
session_id, image_file = sys.argv[1], sys.argv[2]
if not Path(image_file).is_file():
    die(f"✗ Image file not found: {image_file}")
sidecar = api_base("INSTAGRAM_PRIVATE_API_URL", "http://localhost:8011")

boundary = "----skillboundary"
p = Path(image_file)
body = (
    f'--{boundary}\r\nContent-Disposition: form-data; name="picture"; '
    f'filename="{p.name}"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()
    + p.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
)
req = urllib.request.Request(
    f"{sidecar}/account/picture", data=body,
    headers={"X-Session-ID": session_id,
             "Content-Type": f"multipart/form-data; boundary={boundary}"},
    method="PATCH")
print("Uploading profile picture...")
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = r.read().decode()
except urllib.error.URLError as e:
    resp = e.read().decode() if hasattr(e, "read") else str(e)
print(f"Response: {resp}")
print("✓ Profile picture updated (if no error above)")
