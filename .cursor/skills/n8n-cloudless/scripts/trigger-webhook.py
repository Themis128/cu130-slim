#!/usr/bin/env python3
"""Trigger production Cloudless carousel webhook."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, request  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--publish", default="false")
p.add_argument("--slides", type=int, default=7)
p.add_argument("--topic", default="")
p.add_argument("--url", default="")
a = p.parse_args()

url = a.url or api_base("N8N_WEBHOOK_URL", "http://127.0.0.1:5678/webhook/cloudless-carousel")
body = {"num_slides": a.slides, "publish": a.publish.lower() in ("1", "true", "yes")}
if a.topic.strip():
    body["topic"] = a.topic
print(f"POST {url}")
d = request("POST", url, data=body, timeout=600)
print(json.dumps(d, indent=2, ensure_ascii=False))
