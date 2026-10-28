#!/usr/bin/env python3
"""Test the WhatsApp webhook verification endpoint.
Usage: check-webhook.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, env, request_status  # noqa: E402

api = api_base("SOCIAL_API_URL", "http://localhost:8083")
verify_token = env("WHATSAPP_VERIFY_TOKEN", "cloudless_whatsapp_verify")
url = f"{api}/api/v1/whatsapp/webhook"

print("=== WhatsApp Webhook Verification Test ===")
print(f"Endpoint: {url}\n")

code, body = request_status(
    "GET", f"{url}?hub.mode=subscribe&hub.verify_token={verify_token}"
    "&hub.challenge=test_challenge_12345")
print(f"HTTP Status: {code}")
print(f"Response Body: {body}\n")

if code == 200 and body.strip().strip('"') == "test_challenge_12345":
    print("✓ Webhook verification PASSED — Meta can verify this endpoint")
else:
    print("✗ Webhook verification FAILED")
    print("  Expected: 200 + 'test_challenge_12345'")
    print(f"  Got: {code} + '{body}'")
    sys.exit(1)

code, _ = request_status(
    "GET", f"{url}?hub.mode=subscribe&hub.verify_token=wrong_token"
    "&hub.challenge=test_challenge_12345")
print(f"\nWrong token test: HTTP {code} (expected 403)")
print("✓ Correctly rejected invalid verify token" if code == 403
      else "✗ Should have returned 403 for wrong token")
