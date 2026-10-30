#!/usr/bin/env python3
"""meta_2fa_drive.py — drive the FB 2FA enrollment on the phone's native
Facebook app (the only surface that skips Meta's new-device gate).

Chain (each step verified on the S22+ 2026-10-30):
  fb://settings deep-link -> Accounts Center -> Password and security ->
  Two-factor -> Facebook row -> password re-entry -> email confirmation
  code (read instantly via gmail_code_fetch) -> method picker ->
  'Authentication app' -> 'Set up manually' -> read the otpauth secret off
  screen -> store FB_TOTP_SECRET in .env -> enter first TOTP code -> done.

Usage:
  meta_2fa_drive.py                # full drive (stops and prints at each gate)
  meta_2fa_drive.py --pw 'PASS'    # password for re-entry (else prompts)
Requires: adb device + gmail-token.json (mail-code-fetch).
"""
import argparse
import getpass
import os
import re
import subprocess
import sys
import time

ADB = None  # resolved at runtime
GMAIL_FETCH = os.path.join(os.path.dirname(__file__), "..", "..",
                          "mail-code-fetch", "scripts", "gmail_code_fetch.py")
ENV_PATH = r"C:\Users\tbaltzakis\cu130-slim\.env"


def adb(*a):
    dev = ADB
    return subprocess.run(["adb", "-s", dev, *a],
                          capture_output=True, text=True).stdout


def dump():
    return adb("exec-out", "uiautomator", "dump", "/dev/tty")


def texts():
    return [m.group(1) for m in re.finditer(r'text="([^"]+)"', dump())
            if m.group(1)]


def tap(lbl, exact=False):
    xml = dump()
    pat = rf'"{re.escape(lbl)}"' if exact else rf'"[^"]*{re.escape(lbl)}[^"]*"'
    m = re.search(rf'<node[^>]*text={pat}[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
    if not m:
        return False
    adb("shell", "input", "tap",
        str((int(m[1])+int(m[3]))//2), str((int(m[2])+int(m[4]))//2))
    time.sleep(3.5)
    return True


def field_clear_and_type(code):
    xml = dump()
    m = re.search(r'class="android.widget.EditText"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
    if not m:
        return
    adb("shell", "input", "tap", str((int(m[1])+int(m[3]))//2), str((int(m[2])+int(m[4]))//2))
    time.sleep(1)
    # use the field's own clear button (never backspaces)
    adb("shell", "input", "tap", "956", "1214")
    time.sleep(1)
    adb("shell", "input", "text", code)
    time.sleep(1)


def gmail_code():
    out = subprocess.run(
        [sys.executable, "-X", "utf8", os.path.normpath(GMAIL_FETCH),
         "--from", "security@facebookmail.com",
         "--pattern", r"(\d{6}) is your Facebook security code",
         "--exclude", "911250,019400", "--timeout", "150"],
        capture_output=True, text=True)
    c = out.stdout.strip()
    return None if c in ("", "NONE") else c


def save_secret(secret):
    line = f"FB_TOTP_SECRET={secret}\n"
    if os.path.exists(ENV_PATH):
        s = open(ENV_PATH, encoding="utf-8", errors="replace").read()
        if "FB_TOTP_SECRET=" in s:
            s = re.sub(r"^FB_TOTP_SECRET=.*", line.strip(), s, flags=re.M)
        else:
            s += "\n" + line
        open(ENV_PATH, "w", encoding="utf-8").write(s)
    print("FB_TOTP_SECRET stored")


def main():
    global ADB
    ap = argparse.ArgumentParser()
    ap.add_argument("--pw")
    a = ap.parse_args()
    pw = a.pw or os.environ.get("FB_PASSWORD") or getpass.getpass("FB password: ")

    devs = [l.split()[0] for l in subprocess.run(
        ["adb", "devices"], capture_output=True, text=True).stdout.splitlines()[1:]
        if l.strip() and "device" in l and "offline" not in l]
    if not devs:
        print("no adb device — run adb_rearm.py first")
        return
    ADB = devs[0]
    print("adb:", ADB)

    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    adb("shell", "am", "start", "-a", "android.intent.action.VIEW",
        "-d", "fb://settings", "com.facebook.katana")
    time.sleep(6)

    if not tap("Accounts Center"):
        print("settings not on screen:", texts()[:8])
        return
    tap("Password and security")
    tap("Two-factor")
    if not tap("Facebook"):
        print("chooser not up:", texts()[:10])
        return
    time.sleep(3)

    if "re-enter your password" in " ".join(texts()):
        field_clear_and_type(pw)
        tap("Continue")
        time.sleep(4)

    if "confirmation code" in " ".join(texts()).lower():
        tap("Get a new code")
        print("code requested — polling Gmail…")
        code = gmail_code()
        if not code:
            print("no fresh code in 150s — retry")
            return
        print("code:", code)
        field_clear_and_type(code)
        tap("Next")
        time.sleep(6)

    print("screen:", texts()[:16])
    # method picker: choose Authentication app
    if tap("Authentication app") or tap("authenticator"):
        tap("Next")
        time.sleep(4)
        tap("Set up manually") or tap("manually")
        time.sleep(4)
        t = texts()
        for x in t:
            if re.fullmatch(r"[A-Z2-7]{16,}", x.replace(" ", "")):
                save_secret(x.replace(" ", ""))
                break
        print("screen:", t[:16])
        print("enter the first TOTP via meta_restriction_heal.py confirm <code>")
    else:
        print("no method picker — state:", texts()[:16])


if __name__ == "__main__":
    main()
