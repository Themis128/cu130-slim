#!/usr/bin/env python3
"""Suggest hashtags for a topic.
Usage: suggest-hashtags.py "topic" [--platform linkedin]"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import die, request, social_api  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("topic")
p.add_argument("--platform", default="linkedin")
a = p.parse_args()

api, token = social_api()
d = request("POST", f"{api}/api/v1/ai/suggest-hashtags", token=token,
            data={"content": a.topic, "platform": a.platform, "max_hashtags": 5})
if "detail" in d:
    die(f"Error: {d['detail']}")
for t in d.get("hashtags", d.get("data", {}).get("hashtags", [])):
    print(f"#{t}")
