#!/usr/bin/env python3
"""wa_verify_daemon.py — retry WhatsApp request_code hourly until Meta's
rate-limit clears, then dual-poll (ADB+mail) and verify_code."""
import json
import os
import subprocess
import sys
import time

CID = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
TOK = os.environ["WHATSAPP_ACCESS_TOKEN"]

def req() -> tuple[bool, str]:
    """Returns (success, raw_output). Distinguishes Meta auth failures
    (OAuthException 190/460 etc.) from genuine SMS rate-limits — an error
    body containing `"code":190` must NOT be treated as rate-limit retry."""
    r = subprocess.run(
        ["curl", "-s", "-X", "POST",
         f"https://graph.facebook.com/v21.0/{CID}/request_code",
         "-H", f"Authorization: Bearer {TOK}",
         "-d", "code_method=SMS&language=en_US"],
        capture_output=True, text=True)
    out = r.stdout
    try:
        data = json.loads(out)
    except Exception:
        data = {}
    if data.get("success") is True:
        return True, out
    err = data.get("error") or {}
    code = err.get("code")
    sub = err.get("error_subcode")
    # Dead session / revoked token / app-permission loss — retrying hourly
    # can never succeed; bail so the cron doesn't spin for 72h.
    if code in (10, 190) or sub in (460, 459, 463, 467, 368, 2859015):
        return False, f"FATAL auth error code={code} sub={sub}: {out[:200]}"
    return False, out


def main() -> int:
    for _ in range(72):  # 3 days max
        ok, out = req()
        if ok:
            print("code sent:", out[:120])
            # hand off to the dual-poll verifier
            script = str(__import__("pathlib").Path(__file__).parent /
                         "../human-gate-bridges/scripts/wa_verify.py")
            return subprocess.run([sys.executable, script]).returncode
        if out.startswith("FATAL"):
            print(out, flush=True)
            return 2
        print("rate-limited — retrying in 1h", flush=True)
        time.sleep(3600)
    return 1

if __name__ == "__main__":
    sys.exit(main())
