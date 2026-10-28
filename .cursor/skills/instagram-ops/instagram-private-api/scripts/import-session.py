#!/usr/bin/env python3
"""Import an existing Instagram sessionid cookie to bypass login/challenge.
Usage: import-session.py <sessionid>"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, usage  # noqa: E402

sessionid = sys.argv[1] if len(sys.argv) > 1 else usage("import-session.py <sessionid>")
sidecar = api_base("INSTAGRAM_PRIVATE_API_URL", "http://localhost:8011")

print("Importing Instagram session...")
body = urllib.parse.urlencode(
    {"sessionid": sessionid, "locale": "el_GR", "timezone": "10800"}).encode()
req = urllib.request.Request(f"{sidecar}/auth/login/by/sessionid", data=body, method="POST")
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = r.read().decode()
except urllib.error.URLError as e:
    resp = str(e)

if resp.strip().startswith('"'):
    new_session_id = json.loads(resp)
    print("✓ Session imported successfully!")
    print(f"  New Session ID: {new_session_id[:20]}...")
    Path("/tmp/instagram_session_id").write_text(new_session_id)
else:
    print(f"Response: {resp}")
    print("✗ Session import failed — the sessionid may be expired")
