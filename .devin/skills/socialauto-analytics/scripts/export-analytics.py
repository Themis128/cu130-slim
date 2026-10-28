#!/usr/bin/env python3
"""Export analytics report (csv or json).
Usage: export-analytics.py [--format csv|json] [--days N]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login  # noqa: E402
import urllib.request  # noqa: E402

fmt, days = "csv", 30
args = sys.argv[1:]
for i, a in enumerate(args):
    if a == "--format" and i + 1 < len(args):
        fmt = args[i + 1]
    if a == "--days" and i + 1 < len(args):
        days = args[i + 1]

api = api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")
token = api_login(api)
req = urllib.request.Request(
    f"{api}/api/v1/analytics/reports/export?format={fmt}&days={days}",
    headers={"Authorization": f"Bearer {token}"},
)
with urllib.request.urlopen(req, timeout=60) as r:
    sys.stdout.write(r.read().decode())
