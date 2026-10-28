#!/usr/bin/env python3
"""Configure the WhatsApp webhook URL in the Meta App Dashboard via Graph API.
Usage: setup-webhook.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env, request  # noqa: E402

app_id = env("META_APP_ID", "1936126137016578")
app_secret = env("FACEBOOK_APP_SECRET")
access_token = env("WHATSAPP_ACCESS_TOKEN") or env("FACEBOOK_ACCESS_TOKEN")
callback_url = env("WHATSAPP_CALLBACK_URL",
                   "https://social.cloudless.gr/api/v1/whatsapp/webhook")
verify_token = env("WHATSAPP_VERIFY_TOKEN", "cloudless_whatsapp_verify")

if not app_secret or not access_token:
    print("Error: FACEBOOK_APP_SECRET and WHATSAPP_ACCESS_TOKEN must be set")
    print("Set them in .env or as environment variables")
    sys.exit(1)

print("=== WhatsApp Webhook Setup ===")
print(f"App ID: {app_id}")
print(f"Callback URL: {callback_url}")
print(f"Verify Token: {verify_token}\n")

print("1. Registering webhook subscription...")
code, body = __import__("skill_http").request_status(
    "POST", f"https://graph.facebook.com/v21.0/{app_id}/subscriptions",
    data={"object": "whatsapp_business_account",
          "callback_url": callback_url,
          "verify_token": verify_token,
          "fields": ["messages"]})
print(f"   Result: {body}\n")

print("2. If the API call failed, configure manually:")
print(f"   a. Go to: https://developers.facebook.com/apps/{app_id}/whatsapp/")
print("   b. Navigate to: WhatsApp > Configuration")
print(f"   c. Set Callback URL to: {callback_url}")
print(f"   d. Set Verify Token to: {verify_token}")
print("   e. Click 'Verify and Save'")
print("   f. Subscribe to the 'messages' field")
print("\n3. Test the webhook:")
print("   python3 .devin/skills/whatsapp-platform/scripts/check-webhook.py")
