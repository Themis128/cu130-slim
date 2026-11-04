#!/usr/bin/env python3
"""fb_gate_one_shot.py — the entire FB App Secret gate in ONE command.

Waits unattended for the operator to (a) log into facebook.com in the
debug Chrome and (b) type the Meta password into the re-auth dialog.
Everything else is automatic: detect login → open app settings → click
Show → bring window to front + toast → capture secret → env-manager →
verify /oauth/facebook/authorize. Run it and forget it — it will ping
you when your keystroke is needed, and print the result.

Usage: python fb_gate_one_shot.py [--timeout 1800]
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_runner import _ensure_cdp, Page, tabs  # noqa: E402
import fb_secret_reveal as R  # noqa: E402
import fb_secret_ask as A  # noqa: E402

TIMEOUT = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--timeout=")), "1800"))


def fb_logged_in(p):
    try:
        p.goto("https://developers.facebook.com/apps/")
        time.sleep(5)
        t = p.poll("document.body.innerText", timeout=15) or ""
        return "Create App" in t or "My Apps" in t
    except Exception:
        return False


def main():
    _ensure_cdp()
    p = Page()
    deadline = time.time() + TIMEOUT

    # Phase 1: wait for FB session (log in whenever ready — no rush)
    print("phase 1: waiting for facebook.com login in the debug Chrome…")
    A.toast("FB gate armed — log into facebook.com in the debug Chrome when ready")
    while time.time() < deadline:
        if fb_logged_in(p):
            break
        time.sleep(10)
    else:
        print("timeout waiting for login")
        return 1
    print("phase 1 done — FB session live")

    # Phase 2: stage Show → notify → wait for password → capture
    print("phase 2: staging App Secret reveal…")
    print(A.stage())
    A.bring_front()
    A.toast("Type your FB password in the Meta dialog — App Secret will be captured automatically")
    while time.time() < deadline:
        try:
            if not R._dialog_open(p):
                sec = R._secret_value(p)
                if sec:
                    rc = R._envmgr_put("FACEBOOK_CLIENT_SECRET", sec)
                    print(f"secret captured + saved via env-manager (rc={rc})")
                    return 0
        except Exception:
            pass
        time.sleep(4)
    print("timeout — dialog still open")
    return 1


if __name__ == "__main__":
    sys.exit(main())
