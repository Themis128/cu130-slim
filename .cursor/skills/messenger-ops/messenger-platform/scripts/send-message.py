#!/usr/bin/env python3
"""Send a text message to a person on Messenger.
Usage: send-message.py <account_id> <recipient_psid> "message text" """

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

if len(sys.argv) < 4:
    usage('send-message.py <account_id> <psid> "text"')
account_id, psid, text = sys.argv[1], sys.argv[2], sys.argv[3]
api, token = social_api()
print(f"Sending message to PSID {psid}...")
d = request("POST", f"{api}/api/v1/messenger/{account_id}/send", token=token,
            data={"recipient_psid": psid, "text": text, "messaging_type": "RESPONSE"})
print(json.dumps(d, indent=2, ensure_ascii=False))
