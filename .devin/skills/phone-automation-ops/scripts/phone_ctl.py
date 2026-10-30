#!/usr/bin/env python3
"""phone_ctl — resilient ADB UI-drive for the attached Samsung phone.

Wraps the Windows adb binary + device targeting + cover-lock handling +
Play Store install + uiautomator bounds-tap. See SKILL.md for the design.

Usage:
  phone_ctl.py devices|unlock|cover-off|focus|install <pkg>|ui-tap <text>|ui-dump
"""
import re
import subprocess
import sys
import time

ADB = "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe"
DEV = "RFCTA0AEKZP"
COVER_PKGS = ("com.sec.android.cover.ledcover", "com.samsung.android.app.ledbackcover", "com.samsung.android.app.ledcoverdream")

def a(*args, timeout=30):
    return subprocess.run([ADB, "-s", DEV, *args], capture_output=True, text=True, timeout=timeout).stdout

def focus():
    m = re.search(r"mFocusedApp=\S+ (\S+)", a("shell", "dumpsys", "window"))
    return m.group(1) if m else ""

def unlock():
    a("shell", "input", "keyevent", "224"); time.sleep(1)
    a("shell", "input", "swipe", "540", "2000", "540", "400", "300"); time.sleep(2)
    return focus()

def dump(name="ui.xml"):
    a("shell", "uiautomator", "dump", f"/sdcard/{name}")
    return a("shell", "cat", f"/sdcard/{name}")

def tap_text(text):
    xml = dump()
    pat = re.compile(r'(?:text|content-desc)="' + re.escape(text) + r'"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')
    m = pat.search(xml)
    if not m:
        return f"not found: {text}"
    x, y = (int(m[1])+int(m[3]))//2, (int(m[2])+int(m[4]))//2
    a("shell", "input", "tap", str(x), str(y))
    return f"tapped {text} @ {x},{y}"

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "focus"
    if cmd == "devices":
        print(subprocess.run([ADB, "devices"], capture_output=True, text=True).stdout)
    elif cmd == "unlock":
        print("focus:", unlock())
    elif cmd == "focus":
        print("focus:", focus())
    elif cmd == "cover-off":
        for p in COVER_PKGS:
            print(a("shell", "pm", "disable-user", "--user", "0", p).strip()[:80] or p)
    elif cmd == "install" and len(sys.argv) > 2:
        print(a("shell", "am", "start", "-a", "android.intent.action.VIEW",
                "-d", f"market://details?id={sys.argv[2]}", "-p", "com.android.vending"))
    elif cmd == "ui-tap" and len(sys.argv) > 2:
        print(tap_text(" ".join(sys.argv[2:])))
    elif cmd == "wa-greeting":
        # retry daemon: attempts the WA Business greeting toggle every 5min;
        # exits once the switch reports checked. Run detached:
        #   nohup phone_ctl.py wa-greeting &
        for attempt in range(48):  # ~4h
            f = focus()
            if "whatsapp" not in f:
                a("shell", "am", "start", "-n", "com.whatsapp.w4b/com.whatsapp.HomeActivity"); time.sleep(4)
            a("shell", "input", "keyevent", "224"); a("shell", "input", "swipe", "540", "2000", "540", "400", "300"); time.sleep(3)
            xml = dump()
            if "Send greeting message" in xml and 'checked="true"' in xml:
                print("greeting toggle ON — done"); return
            # Tools tab → scroll → Greeting → toggle
            a("shell", "input", "tap", "944", "2144"); time.sleep(3)
            a("shell", "input", "swipe", "540", "1400", "540", "900"); time.sleep(2)
            m = re.search(r'Greeting message[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', dump())
            if m:
                x, y = (int(m[1])+int(m[3]))//2, (int(m[2])+int(m[4]))//2
                a("shell", "input", "tap", str(x), str(y)); time.sleep(3)
                g = dump()
                m2 = re.search(r'bounds="\[0,(\d+)\]\[(\d+),(\d+)\]"[^>]*Send greeting', g)
                if m2:
                    a("shell", "input", "tap", "945", str((int(m2[1])+int(m2[3]))//2)); time.sleep(2)
                    if 'checked="true"' in dump():
                        print("greeting toggled ON — done"); return
            print(f"attempt {attempt}: cover/focus gate — retrying in 5m")
            time.sleep(300)
        print("gave up — do it in-app (Tools → Greeting → toggle)")
    elif cmd == "ui-dump":
        xml = dump()
        for m in re.finditer(r'text="([^"]{3,45})"[^>]*bounds="([^"]*)"', xml):
            print(f"{m.group(1):45s} {m.group(2)}")
    else:
        print(__doc__.strip().splitlines()[-1])

if __name__ == "__main__":
    main()
