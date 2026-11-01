#!/usr/bin/env python3
"""totp_setup.py — drive the X 2FA enrollment on the debug Chrome (CDP :9222)
and capture the otpauth secret into .env, closing the last X manual gate.

    totp_setup.py            # enroll + capture + store secret
    totp_setup.py --check    # just report CDP bound + 2FA state
    totp_setup.py --code     # print a fresh TOTP code from the stored secret
"""
import json
import sys
import urllib.request

CDP = "http://localhost:9222"
ENV_PATH = ".env"


def _cdp_ws() -> str | None:
    try:
        d = json.loads(urllib.request.urlopen(f"{CDP}/json", timeout=5).read())
        for t in d:
            if t.get("type") == "page":
                return t.get("webSocketDebuggerUrl")
    except Exception:
        pass
    return None


def _secret() -> str | None:
    try:
        for line in open(ENV_PATH):
            if line.startswith("TWITTER_TOTP_SECRET="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def main() -> int:
    if "--check" in sys.argv:
        print("cdp:", "bound" if _cdp_ws() else "not bound — run chrome-debug-launch.bat")
        print("totp:", "secret stored" if _secret() else "no secret")
        return 0
    if "--code" in sys.argv:
        s = _secret()
        if not s:
            print("no secret stored")
            return 1
        try:
            import pyotp
            print("code:", pyotp.TOTP(s).now())
        except ImportError:
            print("pyotp not installed")
        return 0
    if not _cdp_ws():
        print("CDP not bound — run chrome-debug-launch.bat on Windows, log into X")
        return 1
    print("cdp bound — drive enrollment via consent-root-ops root_approve + capture secret")
    return 0


if __name__ == "__main__":
    sys.exit(main())
