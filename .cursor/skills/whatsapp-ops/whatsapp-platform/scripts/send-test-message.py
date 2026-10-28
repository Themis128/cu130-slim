#!/usr/bin/env python3
"""Send a test WhatsApp message via the SocialAuto API.
Usage: send-test-message.py <account_id> <recipient_phone> <message>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, request_status, repo_root, usage  # noqa: E402

if len(sys.argv) < 4:
    usage("send-test-message.py <account_id> <recipient_phone> <message>\n"
          "  account_id       UUID of the WhatsApp account in SocialAuto\n"
          "  recipient_phone  E.164 phone (e.g. +3069XXXXXXXX)\n"
          "  message          Text to send")
account_id, recipient, message = sys.argv[1], sys.argv[2], sys.argv[3]
api = api_base("SOCIAL_API_URL", "http://localhost:8083")

token_file = repo_root() / ".whatsapp_test_token"
if not token_file.is_file():
    print(f"No test token found at {token_file}")
    print("Create it with: echo 'your_jwt_token' > " + str(token_file))
    sys.exit(1)
token = token_file.read_text().strip()

print("=== Sending WhatsApp Test Message ===")
print(f"Account: {account_id}")
print(f"To: {recipient}")
print(f"Message: {message}\n")

code, body = request_status("POST", f"{api}/api/v1/whatsapp/{account_id}/send",
                            token=token,
                            data={"to": recipient, "text": message,
                                  "messaging_type": "RESPONSE"})
print(f"HTTP Status: {code}")
print(f"Response: {body}")
if code == 200:
    print("\n✓ Message sent successfully")
else:
    print("\n✗ Failed to send message")
    sys.exit(1)
