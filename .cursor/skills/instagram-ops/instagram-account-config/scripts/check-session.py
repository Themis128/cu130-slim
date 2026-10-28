#!/usr/bin/env python3
"""Check if the Instagram browser session is logged in.
Usage: check-session.py"""

import json
import sys
import time
import urllib.request
from pathlib import Path

BRIDGE = "http://localhost:9223"


def post(path: str, data: dict) -> dict:
    req = urllib.request.Request(
        f"{BRIDGE}{path}", data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


def get(path: str) -> dict:
    try:
        with urllib.request.urlopen(f"{BRIDGE}{path}", timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


print("Session status:")
print(json.dumps(get("/session/status"), indent=2))

print("\nChecking if logged in...")
print(json.dumps(post("/session/navigate",
                      {"url": "https://www.instagram.com/"}), indent=2))
time.sleep(4)

result = post("/session/evaluate", {
    "expression": '(document.body.innerText.includes("Log into") || '
                  'document.body.innerText.includes("Sign up")) '
                  '? "NOT_LOGGED_IN" : "LOGGED_IN"'}).get("result", "")

print()
if result == "LOGGED_IN":
    print("✅ Logged in to Instagram")
    print(post("/session/evaluate", {
        "expression": "document.body.innerText.substring(0, 100)"}).get("result", ""))
else:
    print("❌ Not logged in — open http://localhost:6080/vnc.html and login")
