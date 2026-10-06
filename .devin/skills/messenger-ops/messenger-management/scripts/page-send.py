#!/usr/bin/env python3
"""Send a Page Messenger message.
Usage: page-send.py <account_id> <recipient_psid> <text>"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

if len(sys.argv) < 4:
    usage("page-send.py <account_id> <recipient_psid> <text>")
api, token = social_api()
d = request("POST", f"{api}/api/v1/messenger/{sys.argv[1]}/send", token=token,
            data={"recipient_psid": sys.argv[2], "text": sys.argv[3]})
print(json.dumps(d, indent=2, ensure_ascii=False))
