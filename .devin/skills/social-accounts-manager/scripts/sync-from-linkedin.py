#!/usr/bin/env python3
"""Sync profile info from LinkedIn to all other connected platforms.
Reads the LinkedIn personal profile via SocialAuto, then applies shared
fields (website, about/bio) to Facebook personal + Instagram.
Usage: sync-from-linkedin.py [--apply]   (default: dry-run preview)"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login, request  # noqa: E402

LI_PERSONAL = "18d5cd59-f0c2-4fc4-986e-03601734c7a5"
TARGETS = {
    "facebook_personal": "9355ed63-7787-43e5-a22d-ae0a33d5176b",
    "instagram": "38ddbd44-8811-4d0b-be62-a23fd2f50490",
}

apply = "--apply" in sys.argv
api = api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")
token = api_login(api)

li = request("GET", f"{api}/api/v1/profile/{LI_PERSONAL}", token=token)
fields = {k: li.get(k) for k in ("about", "website", "headline") if li.get(k)}
print("LinkedIn source profile:")
for k, v in fields.items():
    print(f"  {k}: {str(v)[:80]}")

for name, acc_id in TARGETS.items():
    body = {}
    if fields.get("about"):
        body["about"] = fields["about"]
    if fields.get("website"):
        body["website"] = fields["website"]
    if not body:
        continue
    if apply:
        resp = request(
            "PUT", f"{api}/api/v1/profile/{acc_id}", token=token, json_body=body
        )
        print(f"  {name}: applied → {json.dumps(resp)[:120]}")
    else:
        print(f"  {name}: would set {list(body.keys())} (dry-run, pass --apply)")
