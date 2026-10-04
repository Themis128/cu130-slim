#!/usr/bin/env python3
"""Check WhatsApp phone verification status and rate-limit state.
Usage: check-status.py <account_id> [--json]"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else usage("check-status.py <account_id>")
state_file = Path(f"/tmp/whatsapp-verify-{account_id}.state")

api, token = social_api()
d = request("GET", f"{api}/api/v1/whatsapp/{account_id}/phone/status", token=token)

rate_limited = False
cooldown_expires = ""
seconds_until = 0
if state_file.exists():
    state = json.loads(state_file.read_text())
    cooldown_expires = state.get("cooldown_expires_at", "")
    if cooldown_expires:
        try:
            expiry = datetime.fromisoformat(cooldown_expires.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if now < expiry:
                rate_limited = True
                seconds_until = int((expiry - now).total_seconds())
        except ValueError:
            pass

print("Phone: ***")  # display_phone_number is PII — never echoed
status_val = d.get("code_verification_status", "?")
quality_val = d.get("quality_rating", "?")
print(f"Status: {status_val}")
print(f"Quality: {quality_val}")
print(f"Rate limited: {rate_limited}")
if rate_limited:
    print(f"Cooldown expires: {cooldown_expires}")
    print(f"Seconds until cooldown: {seconds_until} "
          f"({seconds_until // 60}m {seconds_until % 60}s)")

if len(sys.argv) > 2 and sys.argv[2] == "--json":
    # Field allowlist: only operational fields leave the box. The state file
    # also holds identifiers (phone numbers) and whatever a future writer
    # adds — a denylist (popping two keys) silently leaks anything new.
    if state_file.exists():
        safe_state = json.loads(state_file.read_text())
        allow = (
            "code_verification_status",
            "quality_rating",
            "cooldown_expires_at",
            "checked_at",
        )
        print(json.dumps({k: safe_state[k] for k in allow if k in safe_state}))
    else:
        print("{}")
