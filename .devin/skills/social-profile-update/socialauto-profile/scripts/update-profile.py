#!/usr/bin/env python3
"""Update profile for a connected social account.
Usage: update-profile.py <account-id> '<json-payload>'"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

if len(sys.argv) < 3:
    usage("update-profile.py <account-id> '<json-payload>'")
payload = json.loads(sys.argv[2])
api, token = social_api()
d = request("PUT", f"{api}/api/v1/profile/{sys.argv[1]}", token=token, data=payload)
print(json.dumps(d, indent=2, ensure_ascii=False))
