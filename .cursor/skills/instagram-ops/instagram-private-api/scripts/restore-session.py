#!/usr/bin/env python3
"""Restore an aiograpi session from saved settings JSON (no password needed).
Usage: restore-session.py <settings_json_file>"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, die, usage  # noqa: E402

sidecar = api_base("INSTAGRAM_PRIVATE_API_URL", "http://localhost:8011")
settings_file = sys.argv[1] if len(sys.argv) > 1 else usage("restore-session.py <settings_json_file>")
if not Path(settings_file).is_file():
    die(f"✗ Settings file not found: {settings_file}")

print("Restoring session from settings...")
req = urllib.request.Request(f"{sidecar}/auth/settings",
                             data=Path(settings_file).read_bytes(),
                             headers={"Content-Type": "application/json"},
                             method="PATCH")
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = r.read().decode()
except urllib.error.URLError as e:
    resp = e.read().decode() if hasattr(e, "read") else str(e)

if resp.strip().startswith('"'):
    session_id = json.loads(resp)
    print("✓ Session restored successfully!")
    print(f"  Session ID: {session_id[:20]}...")
    Path("/tmp/instagram_session_id").write_text(session_id)
else:
    print(f"Response: {resp}")
    print("✗ Session restore failed — settings may be expired")
