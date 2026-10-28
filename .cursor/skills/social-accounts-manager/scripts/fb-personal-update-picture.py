#!/usr/bin/env python3
"""Update Facebook personal picture via SocialAuto profile API.
Usage: fb-personal-update-picture.py /path/to/image.png"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login, upload, usage  # noqa: E402

ACCOUNT_ID = "9355ed63-7787-43e5-a22d-ae0a33d5176b"

path = (
    sys.argv[1]
    if len(sys.argv) > 1
    else usage("fb-personal-update-picture.py /path/to/image.png")
)
api = api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")
token = api_login(api)
resp = upload(
    "POST",
    f"{api}/api/v1/profile/{ACCOUNT_ID}/picture",
    path,
    field="file",
    token=token,
)
print(resp)
