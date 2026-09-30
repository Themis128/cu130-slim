#!/usr/bin/env python3
"""List media library assets.
Usage: list-media.py [--type image|video|generated] [--limit 20] [--search "query"]"""

import argparse
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--type", default="")
p.add_argument("--limit", type=int, default=20)
p.add_argument("--search", default="")
a = p.parse_args()

api, token = social_api()
params = f"page_size={a.limit}"
if a.type:
    params += f"&type={a.type}"
if a.search:
    params += f"&search={urllib.parse.quote(a.search)}"
d = request("GET", f"{api}/api/v1/media/assets?{params}", token=token)
items = d if isinstance(d, list) else d.get("assets", d.get("items", []))
if not items:
    print("No media found.")
    raise SystemExit(0)
for m in items:
    size = m.get("size_bytes", 0) or 0
    sz = f"{size / 1024:.1f}KB" if size < 1048576 else f"{size / 1048576:.1f}MB"
    caption = (m.get("ai_caption") or "")[:30]
    print(f'{m["id"]}  {m.get("mime_type", "?"):20s}  {sz:>10s}  '
          f'{m.get("filename", "?")}  {caption}')
