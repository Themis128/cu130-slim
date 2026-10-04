#!/usr/bin/env python3
"""Auto-verify WhatsApp phone number: polls until rate limit resets, requests
code, waits for user to provide it, verifies, and registers.

Usage: auto-verify.py <account_id> [6-digit-pin] [SMS|VOICE] [language]"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import has_error, phone_status, register_phone, request_code, verify_code  # noqa: E402
from skill_http import usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else \
    usage("auto-verify.py <account_id> [6-digit-pin] [SMS|VOICE] [language]")
pin = sys.argv[2] if len(sys.argv) > 2 else "123456"
method = sys.argv[3] if len(sys.argv) > 3 else "SMS"
language = sys.argv[4] if len(sys.argv) > 4 else "en_US"

print("==========================================")
print(" WhatsApp Phone Auto-Verification")
print("==========================================")
print(f"Account:  {account_id}")
print(f"PIN:      {pin}")
print(f"Method:   {method}")
print(f"Language: {language}")
print("==========================================\n")

print("[1/6] Checking current phone status...")
d = phone_status(account_id)
v = d.get("code_verification_status", "UNKNOWN")
print(f"  code_verification_status: {v}")
if v == "VERIFIED":
    print("  Phone is already VERIFIED — no action needed.")
    sys.exit(0)
_phone = str(d.get("display_phone_number", "?"))
print(f'  display_phone_number: ***{_phone[-4:] if len(_phone) > 4 else "?"}')
print(f'  quality_rating: {d.get("quality_rating", "?")}')

print("\n[2/6] Requesting verification code (polling if rate-limited)...")
MAX_ATTEMPTS = 432
SLEEP_SECONDS = 600
for attempt in range(1, MAX_ATTEMPTS + 1):
    print(f"  Attempt {attempt}/{MAX_ATTEMPTS} at {time.strftime('%Y-%m-%d %H:%M:%S')}...")
    try:
        resp = request_code(account_id, method, language)
    except SystemExit:
        resp = {}
    err = has_error(resp)
    if not err:
        print(f"  ✅ Verification code sent successfully via {method}!")
        break
    if "136024" in err or "rate" in err.lower() or "too many" in err.lower():
        print(f"  ⏳ Rate-limited. Waiting {SLEEP_SECONDS}s before retry...")
        if attempt >= MAX_ATTEMPTS:
            print("  ❌ Max attempts reached. Try again later.")
            sys.exit(1)
        time.sleep(SLEEP_SECONDS)
    else:
        print(f"  ❌ Unexpected error: {err}")
        print(f"  Response: {resp}")
        sys.exit(1)

print("\n[3/6] Enter the 6-digit verification code received on your phone.")
code = input("  Enter 6-digit code: ").strip()
if len(code) != 6:
    print("  ❌ Invalid code. Must be 6 digits.")
    sys.exit(1)

print(f"\n[4/6] Verifying code {code}...")
resp = verify_code(account_id, code)
err = has_error(resp)
if err:
    print(f"  ❌ Verify failed: {err}")
    sys.exit(1)
print("  ✅ Code verified successfully!")

print(f"\n[5/6] Registering phone number with PIN {pin}...")
resp = register_phone(account_id, pin)
err = has_error(resp)
if err:
    print(f"  ❌ Register failed: {err}")
    sys.exit(1)
print("  ✅ Phone registered successfully!")

print("\n[6/6] Confirming final status...")
time.sleep(3)
d = phone_status(account_id)
v = d.get("code_verification_status", "UNKNOWN")
print(f"  code_verification_status: {v}")
if v == "VERIFIED":
    print("\n  🎉 WhatsApp phone verification COMPLETE!")
    print("  The phone number is now registered for Cloud API use.")
else:
    print(f"  ⚠️ Status is {v} — may need a few seconds to propagate.")

print("\n==========================================")
print(" Auto-Verification Complete")
print("==========================================")
