#!/usr/bin/env python3
"""Login to social-api and run CF carousel pipeline.
Never prints passwords or tokens.
Usage: run-pipeline.py [--publish true|false] [--slides N] [--topic T]
       [--account ID] [--api URL]"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env, request, social_api, usage  # noqa: E402

publish = False
slides = 7
topic = env("CLOUDLESS_CAROUSEL_TOPIC",
            "How cloudless.gr helps teams ship serverless apps "
            "without managing servers")
account = env("CLOUDLESS_LINKEDIN_ORG_ACCOUNT_ID",
              "9c4451bb-e820-489f-8676-76ddbc788ffe")
api_override = None

i = 1
while i < len(sys.argv):
    arg = sys.argv[i]
    if arg in ("--publish", "--slides", "--topic", "--account", "--api"):
        if i + 1 >= len(sys.argv):
            usage("run-pipeline.py [--publish true|false] [--slides N] "
                  "[--topic T] [--account ID] [--api URL]")
        val = sys.argv[i + 1]
        if arg == "--publish":
            publish = val.lower() in ("1", "true", "yes")
        elif arg == "--slides":
            slides = int(val)
        elif arg == "--topic":
            topic = val
        elif arg == "--account":
            account = val
        else:
            api_override = val
        i += 2
    else:
        print(f"Unknown arg: {arg}", file=sys.stderr)
        sys.exit(2)

if api_override:
    from skill_http import api_login
    token = api_login(api_override)
    api = api_override
else:
    api, token = social_api()

body = {
    "topic": topic,
    "num_slides": slides,
    "tone": "clear and friendly",
    "include_cta": True,
    "text_model": "@cf/meta/llama-3.2-3b-instruct",
    "txt2img_model": "@cf/black-forest-labs/flux-1-schnell",
    "target_account_id": account,
    "publish": publish,
}

result = request("POST", f"{api}/api/v1/ai/run-carousel-and-publish",
                 token=token, data=body, timeout=600)
print(json.dumps(result, indent=2))
