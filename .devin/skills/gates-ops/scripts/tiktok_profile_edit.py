#!/usr/bin/env python3
"""tiktok_profile_edit.py <website-url> — set the TikTok bio website via ADB.

The field is mobile-app-only (verified: no web surface). Drives the S22+
through adb taps + text input. Coordinates are for TikTok 2026 UI at
1080x2400; verify with `adb shell uiautomator dump` if the layout shifts.
"""
import subprocess
import sys
import time

def sh(*args):
    return subprocess.run(["adb", "shell", *args],
                          capture_output=True, text=True).stdout.strip()

def tap(x, y): sh("input", "tap", str(x), str(y)); time.sleep(1.2)
def text(t): sh("input", "text", t.replace(":", "%3a").replace("/", "%2f").replace(" ", "%s"))
def screencap(name):
    sh("screencap", "-p", "/sdcard/_gate.png")
    subprocess.run(["adb", "pull", "/sdcard/_gate.png", f"/tmp/{name}"],
                   capture_output=True)

def main() -> int:
    if len(sys.argv) < 2:
        print("usage: tiktok_profile_edit.py <website-url>")
        return 2
    url = sys.argv[1]
    print("devices:", subprocess.run(["adb", "devices"], capture_output=True,
                                     text=True).stdout)
    # open own profile
    sh("am", "start", "-n", "com.zhiliaoapp.musically/.home.MainActivity")
    time.sleep(5)
    tap(900, 2200)   # bottom-right Profile tab
    screencap("tt_profile.png")
    tap(540, 720)    # Edit profile button (center card)
    time.sleep(2)
    tap(540, 1500)   # Website field row
    time.sleep(1)
    sh("input", "keyevent", "KEYCODE_MOVE_END")
    text(url)
    tap(540, 260)    # Save (top-right)
    time.sleep(2)
    screencap("tt_after.png")
    print("done — verify /tmp/tt_before/after.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
