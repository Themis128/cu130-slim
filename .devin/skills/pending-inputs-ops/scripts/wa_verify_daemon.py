#!/usr/bin/env python3
"""wa_verify_daemon.py — retry WhatsApp request_code hourly until Meta's
rate-limit clears, then dual-poll (ADB+mail) and verify_code."""
import os
import subprocess
import sys
import time

CID = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
TOK = os.environ["WHATSAPP_ACCESS_TOKEN"]

def req():
    r = subprocess.run(
        ["curl", "-s", "-X", "POST",
         f"https://graph.facebook.com/v21.0/{CID}/request_code",
         "-H", f"Authorization: Bearer {TOK}",
         "-d", "code_method=SMS&language=en_US"],
        capture_output=True, text=True)
    return r.stdout

def main() -> int:
    for _ in range(72):  # 3 days max
        out = req()
        if '"success":true' in out or "code" not in out.lower():
            print("code sent:", out[:120])
            # hand off to the dual-poll verifier
            script = str(__import__("pathlib").Path(__file__).parent /
                         "../human-gate-bridges/scripts/wa_verify.py")
            return subprocess.run([sys.executable, script]).returncode
        print("rate-limited — retrying in 1h", flush=True)
        time.sleep(3600)
    return 1

if __name__ == "__main__":
    sys.exit(main())
