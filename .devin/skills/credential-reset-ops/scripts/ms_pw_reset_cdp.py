#!/usr/bin/env python3
"""ms_pw_reset_cdp.py — Microsoft recovery via REAL Chrome (trusted input).

Drives account.live.com/password/reset through your actual Chrome on
:9222 — CDP trusted input passes Microsoft's credential-form anti-bot
(which rejects the sidecar's synthetic fills).

  ms_pw_reset_cdp.py <email> <new-password>
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_input import CDP  # noqa: E402
from ms_pw_reset import any_code  # noqa: E402


def main():
    email, new_pw = sys.argv[1], sys.argv[2]
    c = CDP()
    print("[1] recovery page")
    c.nav("https://account.live.com/password/reset")
    time.sleep(8)
    print("  ", c.eval("document.title") or "")

    print("[2] email (trusted insert)")
    c.focus_css("input[name=loginfmt],input[type=email],input")
    c.insert_text(email)
    c.click_text("Next")
    time.sleep(6)
    print("  ", (c.eval("document.body.innerText") or "")[:120])

    print("[3] verify contact -> code")
    c.click_text("Get code") or c.click_text("Send code")
    time.sleep(6)

    print("[4] poll code (90s)")
    code = None
    for _ in range(9):
        code = any_code()
        if code:
            break
        time.sleep(10)
    print("  code:", code or "NONE")

    print("[5] code -> new password")
    if code:
        c.focus_css("input")
        c.insert_text(code)
        c.click_text("Next")
        time.sleep(6)
        c.focus_css("input[type=password]")
        c.insert_text(new_pw)
        c.click_text("Next") or c.click_text("Save")
        print("  done — MS password reset via real Chrome")


if __name__ == "__main__":
    main()
