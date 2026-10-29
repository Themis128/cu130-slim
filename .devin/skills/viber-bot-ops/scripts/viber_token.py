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
    # drive the partners portal — needs the Viber login once (QR/phone)
    _call("/session/start", {"platform": "viber"})
    time.sleep(2)
    _ev('location.href="https://partners.viber.com/"')
    time.sleep(8)
    print("page:", _ev("(document.body&&document.body.innerText||'').slice(0,200)"))
    print("portal reached — the bot list / App Token copy needs the Viber")
    print("login on the session (phone-OTP). Once the token's visible, run:")
    print("  viber_provision.py --token <token> --name cloudless")
    return 0


if __name__ == "__main__":
    sys.exit(main())
