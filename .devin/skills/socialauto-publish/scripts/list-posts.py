#!/usr/bin/env python3
"""List recent posts with optional filters.
Usage: list-posts.py [--status draft|scheduled|published|failed] [--limit 10] [--platform linkedin]"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--status", default="")
p.add_argument("--limit", type=int, default=10)
p.add_argument("--platform", default="")
a = p.parse_args()

api, token = social_api()
params = f"limit={a.limit}"
if a.status:
    params += f"&status={a.status}"
if a.platform:
    params += f"&platform={a.platform}"
d = request("GET", f"{api}/api/v1/content/posts?{params}", token=token)
posts = d if isinstance(d, list) else d.get("posts", d.get("items", []))
if not posts:
    print("No posts found.")
for p_ in posts:
    text = (p_.get("content_text") or "")[:60].replace("\n", " ")
    scheduled = (p_.get("scheduled_at") or "-")[:19]
    urls = [t["platform_url"] for t in p_.get("targets", []) if t.get("platform_url")]
    print(f'{p_["id"]}  {p_.get("status", "?"):10s}  {scheduled}  {text}  →  '
          f'{urls[0] if urls else "-"}')
