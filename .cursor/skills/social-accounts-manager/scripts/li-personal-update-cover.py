#!/usr/bin/env python3
"""Update LinkedIn personal cover via SocialAuto profile API.
Usage: li-personal-update-cover.py /path/to/image.png"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login, upload, usage  # noqa: E402

ACCOUNT_ID = "18d5cd59-f0c2-4fc4-986e-03601734c7a5"

path = (
    sys.argv[1]
    if len(sys.argv) > 1
    else usage("li-personal-update-cover.py /path/to/image.png")
)
api = api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")
token = api_login(api)
resp = upload(
    "POST", f"{api}/api/v1/profile/{ACCOUNT_ID}/cover", path, field="file", token=token
)
print(resp)
