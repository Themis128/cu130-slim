#!/usr/bin/env python3
"""Update Instagram external URL (website link in bio).
Usage: update-url.py <session_id> "https://example.com" """

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import sc  # noqa: E402
from skill_http import usage  # noqa: E402

if len(sys.argv) < 3:
    usage('update-url.py <session_id> "https://example.com"')
sid, url = sys.argv[1], sys.argv[2]

print(f"Updating Instagram external URL to {url}...")
d = sc("PATCH", "/account/external-url", sid=sid, form={"external_url": url})
if isinstance(d, dict):
    print(f'Status: {d.get("status", "ok")}')
    print(f'External URL: {d.get("external_url", "N/A")}')
else:
    print(d[:200])
