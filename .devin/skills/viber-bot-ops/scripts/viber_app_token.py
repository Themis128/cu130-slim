#!/usr/bin/env python3
"""viber_app_token.py — drive the Viber app's bot Edit Info via ADB
uiautomator and read the App Token off the screen. The token's app-local
(release app, no run-as) — the UI dump is the automated surface."""
import re
import subprocess
import sys
import time
ADB = "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe"
DEV = "RFCTA0AEKZP"

def sh(*a):
    return subprocess.run([ADB,"-s",DEV,"shell",*a],capture_output=True,text=True,timeout=20).stdout

def ui_text() -> str:
    sh("uiautomator dump /sdcard/ui.xml")
    r = subprocess.run([ADB,"-s",DEV,"shell","cat /sdcard/ui.xml"],capture_output=True,text=True,timeout=20)
    return re.sub(r"<[^>]+>"," ",r.stdout)

def main() -> int:
    sh("am start -n com.viber.voip/.WelcomeActivity")
    time.sleep(5)
    # navigate to the bot — the user's bot-edit flow lands on a token screen
    t = ui_text()
    tok = re.search(r"\b([a-f0-9]{32,64})\b", t)
    if tok:
        print("token:",tok.group(1)[:16]+"…")
        return 0
    print("no token on screen — navigate to the bot's Edit Info, then rerun")
    return 1

if __name__=="__main__": sys.exit(main())
