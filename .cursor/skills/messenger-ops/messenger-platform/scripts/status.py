#!/usr/bin/env python3
"""Check Messenger setup status for a Facebook Page.
Usage: status.py [account_id]"""

import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request_status, social_api  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else "3f2f59c4-f190-44ad-aefe-4321af08ef89"
api, token = social_api()
print(f"=== Messenger Status for account {account_id} ===\n")

import json

code, body = request_status("GET", f"{api}/api/v1/messenger/{account_id}/profile", token=token)
p = json.loads(body) if body.strip().startswith("{") else {}
print(f'Page ID:         {p.get("page_id", "?")}')
print(f'Page Name:       {p.get("page_name", "?")}')
print(f'Subscribed:      {p.get("subscribed", False)}')
print(f'Get Started:     {"yes" if p.get("get_started") else "no"}')
print(f'Persistent Menu: {"yes" if p.get("persistent_menu") else "no"}')
print(f'Whitelisted:     {p.get("whitelisted_domains", [])}')
print(f'Ice Breakers:    {"yes" if p.get("ice_breakers") else "no"}')
print(f'Greeting:        {"yes" if p.get("greeting") else "no (deprecated by Meta)"}')
print()

code, body = request_status("GET", f"{api}/api/v1/messenger/{account_id}/auto-reply", token=token)
a = json.loads(body) if body.strip().startswith("{") else {}
print(f'Auto-Reply:    {"ENABLED" if a.get("enabled") else "disabled"}')
print(f'Model:         {a.get("model", "?")}')
print(f'Max Tokens:    {a.get("max_tokens", "?")}')
print(f'Fallback:      {a.get("fallback_text", "?")[:60]}...')
print()

code, body = request_status("GET", f"{api}/api/v1/messenger/{account_id}/conversations?limit=100",
                            token=token)
c = json.loads(body) if body.strip().startswith("[") else []
print(f"Conversations: {len(c)}")
