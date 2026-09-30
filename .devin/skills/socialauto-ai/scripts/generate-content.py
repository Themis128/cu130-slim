#!/usr/bin/env python3
"""Generate post copy for a specific platform.
Usage: generate-content.py "topic" --platform linkedin --tone professional"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("topic")
p.add_argument("--platform", default="linkedin")
p.add_argument("--tone", default="professional")
p.add_argument("--length", default="medium")
a = p.parse_args()

api, token = social_api()
d = request("POST", f"{api}/api/v1/ai/generate-content", token=token,
            data={"prompt": a.topic, "platform": a.platform,
                  "tone": a.tone, "length": a.length})
print(d.get("data", {}).get("content", d.get("content", "")))
