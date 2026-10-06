#!/usr/bin/env python3
"""Background poller: requests WhatsApp verification code when rate limit resets.
Runs in a loop, checking every 10 minutes, until the code is successfully sent.
Writes status to /tmp/whatsapp-verify-status.json when code is sent.

Usage: poll-rate-limit.py <account_id> [SMS|VOICE] [language]"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import request_code  # noqa: E402
from skill_http import usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else \
    usage("poll-rate-limit.py <account_id> [SMS|VOICE] [language]")
method = sys.argv[2] if len(sys.argv) > 2 else "SMS"
language = sys.argv[3] if len(sys.argv) > 3 else "en_US"
STATUS_FILE = "/tmp/whatsapp-verify-status.json"
MAX_ATTEMPTS = 432
SLEEP_SECONDS = 600


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def error_code(d) -> int:
    if not isinstance(d, dict):
        return 0
    if "error" in d:
        return d["error"].get("code", 0)
    detail = str(d.get("detail", ""))
    if "136024" in detail:
        return 136024
    if "133010" in detail:
        return 133010
    return 0


print(f"[{ts()}] Starting rate-limit poller for account {account_id}")
print(f"[{ts()}] Method: {method}, Language: {language}")
print(f"[{ts()}] Poll interval: 600s (10 min)")
print(f"[{ts()}] Max wait: 72 hours (432 attempts)\n")

for attempt in range(1, MAX_ATTEMPTS + 1):
    print(f"[{ts()}] Attempt {attempt}/{MAX_ATTEMPTS}...")
    try:
        resp = request_code(account_id, method, language)
    except SystemExit:
        resp = {"error": {"message": "script failed", "code": -1}}
    code = error_code(resp)

    if code == 0:
        print(f"[{ts()}] ✅ Verification code sent successfully!")
        Path(STATUS_FILE).write_text(json.dumps({
            "status": "code_sent", "timestamp": ts(),
            "method": method, "account_id": account_id}))
        print(f"[{ts()}] Status written to {STATUS_FILE}")
        print(f"[{ts()}] Now run: auto-verify.py {account_id} <pin> {method} {language}")
        sys.exit(0)
    if code == 133010:
        print(f"[{ts()}] ⚠️ Account not registered (133010). Need to verify first.")
        Path(STATUS_FILE).write_text(json.dumps(
            {"status": "not_registered", "timestamp": ts(), "error": 133010}))
        sys.exit(1)
    if code != 136024:
        print(f"[{ts()}] ❌ Error code {code}: {resp}")
        Path(STATUS_FILE).write_text(json.dumps(
            {"status": "error", "timestamp": ts(), "error_code": code}))
        sys.exit(1)
    print(f"[{ts()}] ⏳ Rate-limited (136024). Waiting {SLEEP_SECONDS}s...")
    time.sleep(SLEEP_SECONDS)

print(f"[{ts()}] ❌ Max attempts ({MAX_ATTEMPTS}) reached. Rate limit window may not have reset.")
Path(STATUS_FILE).write_text(json.dumps(
    {"status": "timeout", "timestamp": ts(), "attempts": MAX_ATTEMPTS}))
sys.exit(1)
