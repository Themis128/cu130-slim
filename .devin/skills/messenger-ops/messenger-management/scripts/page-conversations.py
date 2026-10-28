#!/usr/bin/env python3
"""List Page Messenger conversations.
Usage: page-conversations.py <account_id> [limit]"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else usage("page-conversations.py <account_id> [limit]")
limit = sys.argv[2] if len(sys.argv) > 2 else "25"
api, token = social_api()
d = request("GET", f"{api}/api/v1/messenger/{account_id}/conversations?limit={limit}",
            token=token)
print(json.dumps(d, indent=2, ensure_ascii=False))
