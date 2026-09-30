#!/usr/bin/env python3
"""Generate a LinkedIn carousel (dry-run or publish).
Usage: generate-carousel.py "topic" --slides 7 [--publish false] [--account <id>]
Note: For full custom slides, use the cloudless-carousel-pipeline skill."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("topic")
p.add_argument("--slides", type=int, default=7)
p.add_argument("--publish", default="false")
p.add_argument("--account", default="f65df3e6-a5ef-4d70-ba6c-a568c1d46a7b")
a = p.parse_args()

api, token = social_api()
body = {
    "topic": a.topic,
    "num_slides": a.slides,
    "tone": "clear and friendly",
    "include_cta": True,
    "text_model": "@cf/meta/llama-3.2-3b-instruct",
    "txt2img_model": "@cf/black-forest-labs/flux-1-schnell",
    "target_account_id": a.account,
    "publish": a.publish.lower() in ("1", "true", "yes"),
}
print(f"Generating carousel: {a.topic} ({a.slides} slides, publish={a.publish})")
d = request("POST", f"{api}/api/v1/ai/run-carousel-and-publish",
            token=token, data=body, timeout=280)
print(f'Status: {d.get("status", "?")}')
print(f'Post ID: {d.get("post_id", "?")}')
print(f'Media IDs: {d.get("media_ids", [])}')
print(f'AI Title: {d.get("ai_title", "?")}')
print(f'Slides: {len(d.get("slides", []))}')
for i, s in enumerate(d.get("slides", [])):
    print(f'  Slide {i + 1} ({s.get("slide_type", "?")}): {s.get("title", "?")[:50]}')
if d.get("platform_url"):
    print(f'LinkedIn URL: {d["platform_url"]}')
