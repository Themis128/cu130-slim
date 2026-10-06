#!/usr/bin/env python3
"""Save aiograpi client settings for session restore.
Usage: save-settings.py <session_id> [output_file]"""

import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, usage  # noqa: E402

sidecar = api_base("INSTAGRAM_PRIVATE_API_URL", "http://localhost:8011")
session_id = sys.argv[1] if len(sys.argv) > 1 else usage("save-settings.py <session_id>")
output = sys.argv[2] if len(sys.argv) > 2 else "/tmp/instagram_settings.json"

print(f"Saving settings for session {session_id[:20]}...")
req = urllib.request.Request(f"{sidecar}/auth/settings",
                             headers={"X-Session-ID": session_id})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        Path(output).write_bytes(r.read())
except urllib.error.URLError as e:
    print(f"✗ Failed to save settings: {e}")
    sys.exit(1)

if Path(output).stat().st_size:
    print(f"✓ Settings saved to {output}")
    print(f"  Size: {Path(output).stat().st_size} bytes")
else:
    print("✗ Failed to save settings")
