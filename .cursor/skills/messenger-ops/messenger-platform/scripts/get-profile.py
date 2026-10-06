#!/usr/bin/env python3
"""Get the current Messenger Profile for a Facebook Page.
Usage: get-profile.py [account_id]"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else "3f2f59c4-f190-44ad-aefe-4321af08ef89"
api, token = social_api()
d = request("GET", f"{api}/api/v1/messenger/{account_id}/profile", token=token)
print(json.dumps(d, indent=2, ensure_ascii=False))
