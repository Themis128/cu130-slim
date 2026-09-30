#!/usr/bin/env python3
"""Update Instagram biography.
Usage: update-bio.py <session_id> "new bio text" """

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import sc  # noqa: E402
from skill_http import usage  # noqa: E402

if len(sys.argv) < 3:
    usage('update-bio.py <session_id> "new bio text"')
sid, bio = sys.argv[1], sys.argv[2]

print(f"Updating Instagram biography ({len(bio)} chars)...")
d = sc("PATCH", "/account/biography", sid=sid, form={"biography": bio})
if isinstance(d, dict):
    print(f'Status: {d.get("status", "ok")}')
    print(f'Biography updated: {str(d.get("biography", d.get("updated", "N/A")))[:80]}')
else:
    print(d[:200])
