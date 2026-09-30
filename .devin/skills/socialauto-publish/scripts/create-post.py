#!/usr/bin/env python3
"""Create a quick text-only post (draft or scheduled).
Usage: create-post.py "Your post text" [--platform linkedin] [--schedule "2026-09-01T10:00:00Z"]"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import die, request, social_api  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("text")
p.add_argument("--platform", default="")
p.add_argument("--schedule", default="")
a = p.parse_args()

api, token = social_api()
d = {"content_text": a.text, "status": "scheduled" if a.schedule else "draft"}
if a.schedule:
    d["scheduled_at"] = a.schedule
if a.platform:
    accounts = request("GET", f"{api}/api/v1/accounts", token=token)
    if isinstance(accounts, dict):
        accounts = accounts.get("accounts", accounts.get("items", []))
    ids = [x["id"] for x in accounts
           if x.get("platform") == a.platform and x.get("status") == "active"]
    if not ids:
        die(f"No active account found for platform: {a.platform}")
    d["target_account_ids"] = ids

print("Creating post...")
r = request("POST", f"{api}/api/v1/content/posts", token=token, data=d)
print(f'Post ID: {r.get("id", "?")}')
print(f'Status: {r.get("status", "?")}')
print(f'Scheduled: {r.get("scheduled_at", "-")}')
print(f'Targets: {len(r.get("targets", []))}')
