#!/usr/bin/env python3
"""Update Instagram profile picture.
Usage: update-picture.py <session_id> <image_file>"""

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import sidecar  # noqa: E402
from skill_http import die, repo_root, usage  # noqa: E402

if len(sys.argv) < 3:
    usage("update-picture.py <session_id> <image_file>")
sid, img = sys.argv[1], sys.argv[2]
if not Path(img).is_file():
    die(f"Image file not found: {img}")

print(f"Updating Instagram profile picture with {img}...")
container_path = f"/tmp/ig-profile-pic-{int(__import__('time').time())}.png"
subprocess.run(["docker", "cp", img, f"instagram-private-api:{container_path}"])

boundary = "----skillboundary"
p = Path(img)
body = (
    f'--{boundary}\r\nContent-Disposition: form-data; name="picture"; '
    f'filename="{p.name}"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()
    + p.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
)
req = urllib.request.Request(
    f"{sidecar()}/account/picture", data=body,
    headers={"X-Session-ID": sid,
             "Content-Type": f"multipart/form-data; boundary={boundary}"},
    method="PATCH")
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode()
except urllib.error.URLError as e:
    text = e.read().decode() if hasattr(e, "read") else str(e)

try:
    d = json.loads(text)
    print(f'Status: {d.get("status", "ok")}')
    url = d.get("profile_pic_url") or d.get("user", {}).get("profile_pic_url", "N/A")
    print(f"Profile pic URL: {str(url)[:80]}...")
except ValueError:
    print(text[:200])

subprocess.run(["docker", "compose", "exec", "-T", "instagram-private-api",
                "rm", "-f", container_path], cwd=repo_root(),
               capture_output=True)
