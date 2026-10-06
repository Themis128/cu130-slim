#!/usr/bin/env python3
"""Set up Messenger on a Facebook Page (subscribe + default profile).
Usage: setup.py [account_id] [--greeting "Welcome text"]"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("account_id", nargs="?", default="3f2f59c4-f190-44ad-aefe-4321af08ef89")
p.add_argument("--greeting", default="")
a = p.parse_args()

api, token = social_api()
body = {"greeting_text": a.greeting} if a.greeting else {}
print(f"Setting up Messenger for account {a.account_id}...")
d = request("POST", f"{api}/api/v1/messenger/{a.account_id}/setup", token=token, data=body)
print(f'Status:     {d.get("status", "?")}')
print(f'Page:       {d.get("page_name", "?")}')
print(f'URL:        {d.get("page_url", "?")}')
print(f'Subscribed: {d.get("subscription", {}).get("success", False)}')
prof = d.get("profile", {})
print(f'Profile:    {prof.get("result", "?")}')
if prof.get("result") == "rate_limited":
    print("  NOTE: Profile API rate limited. Subscription succeeded.")
    print("  Retry profile setup in 10 minutes.")
