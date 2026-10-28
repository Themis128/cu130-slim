#!/usr/bin/env python3
"""Improve existing content.
Usage: improve-content.py "existing text" --platform linkedin"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("text")
p.add_argument("--platform", default="linkedin")
a = p.parse_args()

api, token = social_api()
d = request("POST", f"{api}/api/v1/ai/improve-content", token=token,
            data={"content": a.text, "platform": a.platform})
print(d.get("data", {}).get("content", d.get("content", d.get("improved_content", ""))))
