#!/usr/bin/env python3
"""Find the best time to post for a specific account.
Usage: best-time.py --account-id <uuid>"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--account-id", required=True)
p.add_argument("--platform", default=None)  # ignored, backward compat
a = p.parse_args()

api, token = social_api()
d = request("POST", f"{api}/api/v1/ai/best-time-to-post",
            token=token, data={"account_id": a.account_id})
print(json.dumps(d, indent=2, ensure_ascii=False))
