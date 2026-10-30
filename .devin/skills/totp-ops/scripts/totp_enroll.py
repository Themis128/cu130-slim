#!/usr/bin/env python3
"""totp_enroll.py — drive the full X authenticator-app enrollment on the
adhoc browser (:9232) and capture the otpauth secret into .env.

Nav -> Authentication app -> password confirm -> read the secret shown.
"""
import json
import os
import re
import sys
import time
import urllib.request

B = "http://localhost:9232"


def _call(path, body=None, t=25):
    r = urllib.request.Request(B + path, data=json.dumps(body or {}).encode(),
                             headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=t) as f:
            return f.read().decode()[:400]
    except Exception as e:
        return f"err {type(e).__name__}"


def _ev(js):
    return _call("/session/evaluate", {"expression": js})


def _env():
    return dict(l.split("=", 1) for l in open(".env").read().splitlines()
                if "=" in l and not l.startswith("#"))


def main() -> int:
    env = _env()
    pw = env.get("TWITTER_LOGIN_PASSWORD", "")
    if not pw:
        print("no TWITTER_LOGIN_PASSWORD")
        return 1
    _call("/session/start", {"platform": "twitter"})
    time.sleep(2)
    _ev('location.href="https://x.com/settings/account/login_verification/enrollment"')
    time.sleep(8)
    _call("/session/click", {"selector": "text=Authentication app"})
    time.sleep(4)
    _call("/session/fill", {"selector": "input[type=password]", "value": pw})
    time.sleep(2)
    _call("/session/click", {"selector": "button:has-text('Confirm'),button:has-text('Next')"})
    time.sleep(6)
    body = _ev("(document.body&&document.body.innerText||'')")
    m = re.search(r'\b([A-Z2-7]{16,32})\b', body)
    if m:
        secret = m.group(1)
        t = open(".env").read()
        if "TWITTER_TOTP_SECRET" not in t:
            t += f"\nTWITTER_TOTP_SECRET={secret}\n"
            open(".env", "w").write(t)
        # CodeQL: never log the secret — .env is the repo's gitignored secret
        # store; report only that the capture landed.
        print("TOTP secret captured to .env (masked)")
        return 0
    print("no secret found — page:", body[:200])
    return 1


if __name__ == "__main__":
    sys.exit(main())
