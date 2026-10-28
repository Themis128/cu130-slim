#!/usr/bin/env python3
"""Update LinkedIn Organization specialties via browser sidecar.
Usage: li-org-update-specialties.py "Cloud, AI, Software" """

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import usage  # noqa: E402

SIDECAR = "http://localhost:9225"
ORG_VANITY = "cloudless-gr"

val = (
    sys.argv[1]
    if len(sys.argv) > 1
    else usage('li-org-update-specialties.py "Cloud, AI, Software"')
)
specialties = [s.strip() for s in val.split(",") if s.strip()]
req = urllib.request.Request(
    f"{SIDECAR}/company/{ORG_VANITY}/specialties",
    data=json.dumps({"specialties": specialties}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=120) as r:
    print(r.read().decode())
