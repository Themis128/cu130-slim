#!/usr/bin/env python3
"""totp_code.py — generate the current 6-digit code from a stored TOTP secret.

Reads the secret from .env (MS_TOTP_SECRET / FB_TOTP_SECRET / <X>_TOTP_SECRET)
or the authentik-secret slot on omv — the unattended code path for any service
whose authenticator was enrolled via ms_totp_enroll.py.

Usage: totp_code.py [MS|FB|<label>]   -> prints the current code
       totp_code.py --all             -> all stored secrets
"""
import sys


def secrets():
    out = {}
    try:
        for ln in open("/mnt/c/Users/tbaltzakis/cu130-slim/.env").readlines():
            if "_TOTP_SECRET=" in ln and "=" in ln:
                k, v = ln.strip().split("=", 1)
                out[k.replace("_TOTP_SECRET", "")] = v.strip().replace(" ", "")
    except OSError:
        pass
    return out


def code_of(secret):
    try:
        import pyotp
        return pyotp.TOTP(secret).now()
    except ImportError:
        import base64
        import hashlib
        import hmac
        import struct
        import time as t
        counter = int(t.time() // 30)
        key = base64.b32decode(secret.upper())
        hs = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
        off = hs[-1] & 0x0F
        return "%06d" % ((int.from_bytes(hs[off:off + 4], "big") & 0x7FFFFFFF) % 10 ** 6)


def main():
    secs = secrets()
    if "--all" in sys.argv or len(sys.argv) < 2:
        for k, s in secs.items():
            print(f"{k}: {code_of(s)}")
        if not secs:
            print("no *_TOTP_SECRET in .env")
        return
    k = sys.argv[1].upper()
    s = secs.get(k)
    if not s:
        sys.exit(f"no {k}_TOTP_SECRET — enroll via ms_totp_enroll.py first")
    print(code_of(s))


if __name__ == "__main__":
    main()
