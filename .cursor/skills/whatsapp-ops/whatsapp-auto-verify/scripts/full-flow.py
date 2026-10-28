#!/usr/bin/env python3
"""Full WhatsApp phone verification flow with rate-limit handling.
Orchestrates: check-status → wait-and-request → prompt for code →
verify-and-register → confirm.

Usage: full-flow.py <account_id> [SMS|VOICE] [language] [pin]"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import usage  # noqa: E402

if len(sys.argv) < 2:
    usage("full-flow.py <account_id> [SMS|VOICE] [language] [pin]")
account_id = sys.argv[1]
code_method = sys.argv[2] if len(sys.argv) > 2 else "SMS"
language = sys.argv[3] if len(sys.argv) > 3 else "en_US"
pin = sys.argv[4] if len(sys.argv) > 4 else ""
script_dir = Path(__file__).resolve().parent


def run(script: str, *args: str) -> int:
    return subprocess.run([sys.executable, str(script_dir / script), *args]).returncode


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


err("========================================")
err("  WhatsApp Phone Verification")
err(f"  Account: {account_id}")
err(f"  Method:  {code_method}")
err(f"  Language: {language}")
err("========================================\n")

err("=== Step 1: Check current status ===")
run("check-status.py", account_id)

err("\n=== Step 2: Request verification code ===")
if run("wait-and-request.py", account_id, code_method, language) != 0:
    err("\n❌ Could not request verification code.")
    err("The rate limit may still be active. Try again later.")
    sys.exit(1)

err("\n=== Step 3: Enter verification code ===")
err(f"A {code_method} verification code was sent to the phone number.")
code = input("Enter the 6-digit code: ").strip()
if not code:
    err("❌ No code entered. Aborting.")
    sys.exit(1)

err("\n=== Step 4: Verify code and register ===")
result = run("verify-and-register.py", account_id, code, pin)

if result == 0:
    err("\n========================================")
    err("  ✅ WhatsApp phone verification complete")
    err("========================================")
else:
    err("\n========================================")
    err("  ❌ WhatsApp phone verification failed")
    err("========================================")
sys.exit(result)
