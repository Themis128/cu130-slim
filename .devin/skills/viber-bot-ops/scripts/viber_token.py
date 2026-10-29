#!/usr/bin/env python3
"""viber_token.py — drive the Viber partners portal on the adhoc browser to
create/select the Cloudless bot and capture the App Token, then auto-run
viber_provision (connect → validate → webhook).
    viber_token.py           # portal drive → token → provision
    viber_token.py --check   # is a token already provisioned?
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

B = "http://localhost:9232"


def _call(path, body=None, t=25):
    r = urllib.request.Request(B + path, data=json.dumps(body or {}).encode(),
                             headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=t) as f:
            return f.read().decode()[:300]
    except Exception as e:
        return f"err {type(e).__name__}"


def _ev(js):
    return _call("/session/evaluate", {"expression": js})


def _connected() -> bool:
    r = subprocess.run(["docker", "compose", "exec", "-T", "social-api",
                        "python", "scripts/viber_tool.py", "info"],
                       capture_output=True, text=True, timeout=30)
    return bool(r.stdout.strip()) and "no viber accounts" not in r.stdout.lower()


def main() -> int:
    if "--check" in sys.argv:
        print("viber:", "connected" if _connected() else "no accounts — needs token")
        return 0 if _connected() else 1
    # full OTP-driven portal login — phone number → OTP via adb_sms → token
    import adb_sms
    phone = os.environ.get("VIBER_PORTAL_PHONE", "")
    _call("/session/start", {"platform": "viber"})
    time.sleep(2)
    _ev('location.href="https://partners.viber.com/login"')
    time.sleep(8)
    if phone:
        _call("/session/fill", {"selector": "input[type=tel],input[type=text] >> nth=0", "value": phone})
        time.sleep(1)
        _call("/session/click", {"selector": "button[type=submit],button:has-text('Continue'),button:has-text('Send')"})
        time.sleep(8)
        code, row = adb_sms.read_code()
        if code:
            _call("/session/fill", {"selector": "input[type=tel],input[type=text] >> nth=0", "value": code})
            time.sleep(1)
            _call("/session/click", {"selector": "button[type=submit],button:has-text('Verify'),button:has-text('Log')"})
            time.sleep(8)
            print("logged in — page:", _ev("(document.body&&document.body.innerText||'').slice(0,150)"))
            # capture the App Token from the bot page
            tok = _ev("((document.body.innerText||'').match(/app[_ ]?token[:\s]+([a-f0-9]{32,})/i)||[])[1]||''")
            if tok:
                r = subprocess.run(["python3",
                                    ".devin/skills/viber-ops/scripts/viber_provision.py",
                                    "--token", tok, "--name", "cloudless"],
                                   capture_output=True, text=True, timeout=60)
                print("provision:", r.stdout[:150])
            else:
                print("no token found on page — check the bot list manually")
        else:
            print("no OTP SMS yet — the login's armed; rerun after the code lands")
    else:
        print("VIBER_PORTAL_PHONE unset — set the account phone to drive the login")
    return 0


if __name__ == "__main__":
    sys.exit(main())
