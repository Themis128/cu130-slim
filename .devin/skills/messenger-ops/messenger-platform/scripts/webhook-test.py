#!/usr/bin/env python3
"""Test webhook verification and event processing.
Usage: webhook-test.py [--verify | --message "text" | --postback "payload" | --empty]"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, env, request_status  # noqa: E402

api = api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")
PAGE_ID = "116436681562585"
VERIFY_TOKEN = env("MESSENGER_VERIFY_TOKEN", "cloudless_messenger_verify")
HOOK = f"{api}/api/v1/messenger/webhook"


def test_verify():
    print("=== Webhook GET verification ===")
    print("--- Correct token ---")
    code, body = request_status(
        "GET", f"{HOOK}?hub.mode=subscribe&hub.verify_token={VERIFY_TOKEN}&hub.challenge=test123")
    print(f"  HTTP {code}, Body: {body}")
    print("  PASS" if code == 200 and body.strip() == '"test123"' else "  FAIL")
    print("--- Wrong token ---")
    code, _ = request_status(
        "GET", f"{HOOK}?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=test123")
    print(f"  HTTP {code}")
    print("  PASS" if code == 403 else "  FAIL")
    print("--- Missing params ---")
    code, _ = request_status("GET", HOOK)
    print(f"  HTTP {code}")
    print("  PASS" if code in (400, 403, 422) else "  FAIL")


def post(payload):
    return request_status("POST", HOOK, data=payload)


def test_message(text="Hello from test"):
    print(f"=== Webhook POST (text message) ===")
    code, body = post({"object": "page", "entry": [{"id": PAGE_ID, "messaging": [{
        "sender": {"id": "test_psid_123"}, "recipient": {"id": PAGE_ID},
        "message": {"mid": "m_test", "text": text}}], "time": 1700000000000}]})
    print(f"  HTTP {code}, Body: {body}")
    print("  PASS" if code == 200 else "  FAIL")


def test_postback(payload="GET_STARTED"):
    print(f"=== Webhook POST (postback: {payload}) ===")
    code, body = post({"object": "page", "entry": [{"id": PAGE_ID, "messaging": [{
        "sender": {"id": "test_psid_456"}, "recipient": {"id": PAGE_ID},
        "postback": {"payload": payload}}], "time": 1700000000001}]})
    print(f"  HTTP {code}, Body: {body}")
    print("  PASS" if code == 200 else "  FAIL")


def test_empty():
    print("=== Webhook POST (empty body) ===")
    code, body = request_status("POST", HOOK, data={})
    print(f"  HTTP {code}, Body: {body}")
    print("  PASS" if code == 200 else "  FAIL")


p = argparse.ArgumentParser()
p.add_argument("--verify", action="store_true")
p.add_argument("--message", nargs="?", const="Hello from test")
p.add_argument("--postback", nargs="?", const="GET_STARTED")
p.add_argument("--empty", action="store_true")
a = p.parse_args()

if not any([a.verify, a.message, a.postback, a.empty]):
    test_verify(); print()
    test_empty(); print()
    test_message("Hello from test"); print()
    test_postback("GET_STARTED")
else:
    if a.verify:
        test_verify()
    if a.message:
        test_message(a.message)
    if a.postback:
        test_postback(a.postback)
    if a.empty:
        test_empty()
