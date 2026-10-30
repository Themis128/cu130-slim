#!/usr/bin/env python3
"""wa_code_watch.py — poll WhatsApp request_code until Meta's SMS cooldown
clears, fire it once a code can be sent, then report. The wa_verify daemon
handles the SMS-code read via ADB after the code lands.
    wa_code_watch.py        # retry loop until code sent or 90 min
    wa_code_watch.py --once # single attempt
"""
import subprocess
import sys
import time

API = ["docker", "compose", "exec", "-T", "social-api",
       "python", "scripts/whatsapp_tool.py"]


def _try() -> str:
    try:
        r = subprocess.run(API + ["request_code"], capture_output=True,
                          text=True, timeout=40)
        return r.stdout + r.stderr
    except Exception as e:
        return f"err {e}"


def main() -> int:
    once = "--once" in sys.argv
    for _ in range(90 if not once else 1):
        out = _try()
        if "fbtrace" not in out and "Couldn't Be Sent" not in out and "error" not in out.lower():
            print("code requested:", out[:200])
            return 0
        print("still throttled — retrying in 60s" if not once else "throttled")
        if once:
            return 1
        time.sleep(60)
    return 1


if __name__ == "__main__":
    sys.exit(main())
