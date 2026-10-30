#!/usr/bin/env python3
"""viber_phone_capture — pull the bot auth token off the Viber mobile app
via ADB UI-automation (phone must be plugged in, USB-debugging on).

    viber_phone_capture.py run     # navigate app → dump UI → extract → provision
    viber_phone_capture.py devices # is a phone attached?
"""
import re
import subprocess
import sys
import time

SKILLS = "/home/tbaltzakis/cu130-slim/.devin/skills"
TOKEN_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f-]{30,}", re.I)


def _adb(*args) -> str:
    r = subprocess.run(["adb"] + list(args),
                       capture_output=True, text=True, timeout=30)
    return r.stdout + r.stderr


def devices() -> int:
    out = _adb("devices")
    devs = [l for l in out.splitlines() if "\tdevice" in l]  # noqa: E741
    print("devices:", len(devs) or "none — plug the phone (USB-debugging on)")
    return 0 if devs else 2


def run() -> int:
    if devices() != 0:
        return 2
    # wake + open Viber
    _adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    _adb("shell", "monkey", "-p", "com.viber.voip", "1")
    time.sleep(6)
    # dump the current UI tree → find token-shaped text on bot info screens
    for _ in range(3):
        xml = _adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
        xml += _adb("shell", "cat", "/sdcard/ui.xml")
        tok = TOKEN_RE.search(xml)
        if tok:
            print("token candidate:", tok.group(0)[:12] + "…")
            r = subprocess.run(
                ["python3", f"{SKILLS}/viber-ops/scripts/viber_provision.py",
                 "--token", tok.group(0)],
                capture_output=True, text=True, timeout=60)
            print("provision:", "ok" if r.returncode == 0 else r.stderr[:100])
            return r.returncode
        # swipe to scroll deeper, then re-dump
        _adb("shell", "input", "swipe", "500", "1500", "500", "500")
        time.sleep(2)
    print("no token found — navigate Viber → bot → Info/Admin screen first")
    return 1


if __name__ == "__main__":
    sys.exit({"run": run, "devices": devices}[
             sys.argv[1] if len(sys.argv) > 1 else "devices"]())
