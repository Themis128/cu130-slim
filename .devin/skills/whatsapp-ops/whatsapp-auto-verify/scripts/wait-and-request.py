#!/usr/bin/env python3
"""Wait for rate-limit cooldown to expire, then request a WhatsApp
verification code. Handles Meta error 136024 automatically.

Usage: wait-and-request.py <account_id> [SMS|VOICE] [language]"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request_status, social_api, usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else \
    usage("wait-and-request.py <account_id> [SMS|VOICE] [language]")
code_method = sys.argv[2] if len(sys.argv) > 2 else "SMS"
language = sys.argv[3] if len(sys.argv) > 3 else "en_US"
state_file = Path(f"/tmp/whatsapp-verify-{account_id}.state")

api, token = social_api()
url = f"{api}/api/v1/whatsapp/{account_id}/phone"


def cooldown_remaining() -> int:
    if not state_file.exists():
        return 0
    try:
        state = json.loads(state_file.read_text())
        expiry = state.get("cooldown_expires_at", "")
        if not expiry:
            return 0
        e = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        return max(0, int((e - datetime.now(timezone.utc)).total_seconds()))
    except (ValueError, OSError):
        return 0


remaining = cooldown_remaining()
if remaining > 0:
    print(f"⏳ Rate-limited. Waiting {remaining}s for cooldown to expire...", file=sys.stderr)
    while remaining > 0:
        s = min(30, remaining)
        print(f"  {remaining}s remaining...", file=sys.stderr)
        time.sleep(s)
        remaining -= s
    print("✅ Cooldown expired", file=sys.stderr)

print(f"=== Requesting {code_method} code for {account_id} ===", file=sys.stderr)
code, body = request_status("POST", f"{url}/request-code", token=token,
                            data={"code_method": code_method, "language": language})
try:
    d = json.loads(body)
except ValueError:
    d = {}
detail = str(d.get("detail", ""))
if "136024" in detail or "wait 1 hour" in detail.lower() or "too many" in detail.lower():
    print("❌ Rate-limited by Meta. Saving cooldown state...", file=sys.stderr)
    now = datetime.now(timezone.utc)
    state_file.write_text(json.dumps({
        "account_id": account_id,
        "rate_limited_at": now.isoformat(),
        "cooldown_expires_at": (now + timedelta(hours=1)).isoformat(),
        "last_code_request_at": now.isoformat(),
        "last_error": "136024",
    }, indent=2))
    print(f"State saved to {state_file}")
    print("⏳ Try again in 1 hour. Run this script again after the cooldown.", file=sys.stderr)
    sys.exit(1)

if "error" not in detail.lower() and code == 200:
    print(f"✅ Verification code sent via {code_method}", file=sys.stderr)
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    state["last_code_request_at"] = datetime.now(timezone.utc).isoformat()
    state["rate_limited_at"] = ""
    state["cooldown_expires_at"] = ""
    state_file.write_text(json.dumps(state, indent=2))
    sys.exit(0)

print("⚠️ Unexpected response:", file=sys.stderr)
try:
    print(json.dumps(d, indent=2))
except ValueError:
    print(body)
sys.exit(1)
