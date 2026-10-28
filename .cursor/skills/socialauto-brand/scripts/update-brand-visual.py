#!/usr/bin/env python3
"""Update brand visual identity (colors, fonts, logo).
Usage: update-brand-visual.py <primary_color> <accent_color> <heading_font> <body_font> [logo_media_id]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

if len(sys.argv) < 5:
    usage("update-brand-visual.py <primary_color> <accent_color> "
          "<heading_font> <body_font> [logo_media_id]")
body = {
    "primary_color": sys.argv[1],
    "accent_color": sys.argv[2],
    "font_heading": sys.argv[3],
    "font_body": sys.argv[4],
}
if len(sys.argv) > 5:
    body["logo_url"] = f"/api/v1/media/view?path={sys.argv[5]}"
api, token = social_api()
d = request("PUT", f"{api}/api/v1/brand/visual", token=token, data=body)
print(f'Visual updated: primary={d.get("primary_color")}, accent={d.get("accent_color")}')
print(f'Fonts: {d.get("font_heading")} / {d.get("font_body")}')
if d.get("logo_url"):
    print(f'Logo: {d["logo_url"]}')
