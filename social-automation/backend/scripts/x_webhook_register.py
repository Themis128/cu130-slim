"""Register an X webhook + subscribe to Account Activity events via API.

Uses OAuth1 user-context against the Account Activity API:
  POST /1.1/account_activity/all/{env}/webhooks.json?url={webhook_url}
  POST /1.1/account_activity/all/{env}/subscriptions.json
  GET  /1.1/account_activity/all/{env}/webhooks.json  (list/verify)

Usage: python x_webhook_register.py <env_name> <webhook_url>
"""
from __future__ import annotations

import json
import sys
from urllib.parse import quote

from requests_oauthlib import OAuth1Session

ENV = sys.argv[1] if len(sys.argv) > 1 else "prod"
WEBHOOK_URL = sys.argv[2] if len(sys.argv) > 2 else "https://social.cloudless.gr/api/v1/webhooks/twitter"

import os
API_KEY = os.environ.get("TWITTER_API_KEY", "")
API_SECRET = os.environ.get("TWITTER_API_SECRET", "")
ACCESS = os.environ.get("TWITTER_ACCESS_TOKEN", "")
ACCESS_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "")

s = OAuth1Session(API_KEY, API_SECRET, ACCESS, ACCESS_SECRET)
base = f"https://api.x.com/1.1/account_activity/all/{ENV}"


def register() -> None:
    r = s.post(f"{base}/webhooks.json?url={quote(WEBHOOK_URL, safe='')}")
    print("register:", r.status_code, r.text[:200])


def subscribe() -> None:
    r = s.post(f"{base}/subscriptions.json")
    print("subscribe:", r.status_code, r.text[:200])


def list_hooks() -> None:
    r = s.get(f"{base}/webhooks.json")
    print("webhooks:", r.status_code, r.text[:300])


if __name__ == "__main__":
    register()
    subscribe()
    list_hooks()
