#!/usr/bin/env python3
"""Update Instagram bio (150 char limit, emojis count as 2) via instagrapi sidecar.
Usage: ig-update-bio.py "Bio text" """

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import IG_API, ig_sid, http  # noqa: E402
from skill_http import usage  # noqa: E402

bio = sys.argv[1] if len(sys.argv) > 1 else usage('ig-update-bio.py "Bio text"')
sid = ig_sid()

print(f"→ Updating Instagram bio ({len(bio)} chars)...")
text = http("PATCH", f"{IG_API}/account/biography",
            headers={"X-Session-ID": sid}, form={"biography": bio})
try:
    d = json.loads(text)
    print(f'  Result: {d.get("status", "ok")}')
    print(f'  Bio: {str(d.get("biography", d.get("updated", "N/A")))[:80]}')
except ValueError:
    print(text[:200])
