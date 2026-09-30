#!/usr/bin/env python3
"""Update brand DNA (name, industry, tagline, mission, values, website).
Usage: update-brand-dna.py <name> <tagline> <website> [mission]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

if len(sys.argv) < 4:
    usage("update-brand-dna.py <name> <tagline> <website> [mission]")
body = {"name": sys.argv[1], "tagline": sys.argv[2], "website_url": sys.argv[3]}
if len(sys.argv) > 4:
    body["mission"] = sys.argv[4]
api, token = social_api()
d = request("PUT", f"{api}/api/v1/brand", token=token, data=body)
print(f'Brand updated: {d.get("name", "?")} - {d.get("tagline", "?")}')
