#!/usr/bin/env python3
"""meta_totp.py — capture Meta TOTP secret; print codes on demand.

--status   report whether META_TOTP_SECRET is set and generates a code
--code     print the current TOTP code
--capture  open the Meta 2FA enrollment page in the debug browser and
           print the otpauth secret it surfaces (save it to .env)

Requires pyotp for --code/--status: pip install pyotp
"""
import json
import os
import re
import sys
import urllib.request

CDP = os.environ.get("CDP_URL", "http://100.83.120.93:9223")
TWOFA = "https://accountscenter.facebook.com/password_and_security/two_factor"


def secret() -> str | None:
    for path in (".env", "../.env", os.path.expanduser("~/.env")):
        if os.path.exists(path):
            for line in open(path):
                if line.startswith("META_TOTP_SECRET="):
                    v = line.split("=", 1)[1].strip()
                    return v or None
    return os.environ.get("META_TOTP_SECRET")


def code() -> str | None:
    s = secret()
    if not s:
        return None
    try:
        import pyotp
        return pyotp.TOTP(s).now()
    except ImportError:
        return "PYOTP_MISSING"


def capture() -> str:
    try:
        targets = json.load(urllib.request.urlopen(CDP + "/json", timeout=8))
    except Exception as e:
        return f"CDP down: {e}"
    t = next((x for x in targets if x["type"] == "page"), None)
    if not t:
        return "no page"
    try:
        import websocket
    except ImportError:
        return "websocket-client missing"
    ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=15,
                                     suppress_origin=True)
    ws.send(json.dumps({"id": 1, "method": "Page.navigate",
                        "params": {"url": TWOFA}}))
    ws.recv()
    import time
    time.sleep(7)
    ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {
        "expression": """(() => {
          const a=[...document.querySelectorAll('a[href*=otpauth],[data-otp]')];
          const m=document.body && document.body.innerHTML.match(/otpauth:[^"'\\s<]+/);
          return (a[0]&&a[0].href)||(m&&m[0])||'no-secret-yet';
        })()""", "returnByValue": True}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == 2:
            v = r.get("result", {}).get("result", {}).get("value", "?")
            ws.close()
            m = re.search(r"secret=([A-Z2-7]+)", v or "")
            return m.group(1) if m else f"surfaced:{v[:80]}"


def main() -> int:
    if "--status" in sys.argv:
        s = secret()
        print("secret:", "set" if s else "NOT SET")
        if s:
            print("code:", code())
        return 0
    if "--code" in sys.argv:
        print(code() or "no secret"); return 0
    if "--capture" in sys.argv:
        print(capture()); return 0
    print("usage: --status | --code | --capture"); return 1


if __name__ == "__main__":
    sys.exit(main())
