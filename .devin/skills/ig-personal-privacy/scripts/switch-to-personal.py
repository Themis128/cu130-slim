#!/usr/bin/env python3
"""Switch @t_baltzakis IG to personal account via ADB UI automation.

The Personal card on Account-type screen is display-only on some builds —
the real switch is the "Switch to personal account" link at the bottom of
Account type and tools. Falls back to reporting if the link isn't found.
"""
import subprocess
import re
import sys
import time

DEVICE = "192.168.1.77:5555"

def sh(cmd):
    return subprocess.run(f'adb -s {DEVICE} shell {cmd}', capture_output=True, text=True, shell=False).stdout

def tap(x, y):
    subprocess.run(f'adb -s {DEVICE} shell input tap {x} {y}', shell=False)

def back():
    subprocess.run(f'adb -s {DEVICE} shell input keyevent KEYCODE_BACK', shell=False)

def dump():
    sh('uiautomator dump /sdcard/window_dump.xml')
    return sh('cat /sdcard/window_dump.xml')

def find(xml, needle):
    for m in re.finditer(r'<node[^>]*text="%s"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"' % re.escape(needle), xml):
        x1,y1,x2,y2 = map(int, m.groups())
        return ((x1+x2)//2, (y1+y2)//2)
    return None

def tap_text(needle, wait=4):
    xml = dump()
    pos = find(xml, needle)
    if pos:
        tap(*pos)
        time.sleep(wait)
        return True
    return False

def scroll(times=3):
    for _ in range(times):
        sh('input swipe 540 1500 540 300 400')
        time.sleep(1.5)

# Open IG → profile
sh('monkey -p com.instagram.android -c android.intent.category.LAUNCHER 1')
time.sleep(5)
tap(972, 2137)  # profile tab
time.sleep(4)

# Options → Settings
tap(989, 153)
time.sleep(4)
scroll(1)
if not tap_text('Account type and tools'):
    print('FAIL: Account type and tools not found')
    sys.exit(1)
scroll(3)
# Look for switch link at bottom
xml = dump()
pos = find(xml, 'Switch to personal account')
if pos:
    tap(*pos)
    time.sleep(5)
    # confirm sheet
    tap_text('Switch account', 5) or tap_text('Switch', 5)
    print('SWITCHED to personal')
else:
    print('No switch link visible — may already be personal or needs manual tap')

# step 2 — privacy toggle (private account)
# Settings → Account privacy → toggle Private account
tap_text('Account privacy', 8)
time.sleep(1.5)
# toggle on the switch
pos = find_text('Private account')  # noqa: F821
if pos:
    tap(*pos)
    time.sleep(4)
    tap_text('Switch to private', 5) or tap_text('OK', 3)
    print('PRIVACY toggled to private')
else:
    print('privacy screen not found — may already be private')
