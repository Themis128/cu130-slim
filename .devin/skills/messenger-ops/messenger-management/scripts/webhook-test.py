#!/usr/bin/env python3
"""Test Messenger webhook verification + event dispatch.
Usage: webhook-test.py [page_id]"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, env, request  # noqa: E402

api = api_base("SOCIAL_API_URL", "http://localhost:8083")
page_id = sys.argv[1] if len(sys.argv) > 1 else "116436681562585"
verify_token = env("MESSENGER_VERIFY_TOKEN", "cloudless_messenger_verify")

print("=== 1. Webhook Verification (GET) ===")
d = request("GET",
            f"{api}/api/v1/messenger/webhook?hub.mode=subscribe"
            f"&hub.verify_token={verify_token}&hub.challenge=test123", raw=True)
print(d)

print("\n=== 2. Webhook Event Dispatch (POST) ===")
mid = f"m_test_{int(time.time())}"
d = request("POST", f"{api}/api/v1/messenger/webhook", data={
    "object": "page",
    "entry": [{
        "id": page_id,
        "messaging": [{
            "sender": {"id": "test_psid"},
            "recipient": {"id": page_id},
            "message": {"mid": mid, "text": "Test from webhook-test.py"},
        }],
        "time": int(time.time() * 1000),
    }],
})
print(json.dumps(d, indent=2, ensure_ascii=False))

time.sleep(2)
print("\n=== 3. Sidecar Stats ===")
d = request("GET", "http://localhost:9230/stats")
print(json.dumps(d, indent=2, ensure_ascii=False))
