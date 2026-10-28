#!/usr/bin/env python3
"""Update Instagram external URL via instagrapi sidecar.
Usage: ig-update-url.py "https://cloudless.gr" """

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import IG_API, ig_sid, http  # noqa: E402
from skill_http import usage  # noqa: E402

url = sys.argv[1] if len(sys.argv) > 1 else usage('ig-update-url.py "https://cloudless.gr"')
sid = ig_sid()

print(f"→ Updating Instagram external URL to {url}...")
text = http("PATCH", f"{IG_API}/account/external-url",
            headers={"X-Session-ID": sid}, form={"external_url": url})
try:
    d = json.loads(text)
    print(f'  Result: {d.get("status", "ok")}')
    print(f'  URL: {d.get("external_url", "N/A")}')
except ValueError:
    print(text[:200])
