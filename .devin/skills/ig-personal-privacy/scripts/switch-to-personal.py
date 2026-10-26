#!/usr/bin/env python3
"""Switch IG t_baltzakis Creator→Personal→Private on the Galaxy S22+.

Uses uiautomator2 accessibility clicks — raw `input tap` fails on IG's
React-native cards. Phone must be unlocked and on Wi-Fi ADB.
"""
import re, sys, time
import uiautomator2 as u2

D = u2.connect("192.168.1.77:5555")

def texts():
    return [t for t in re.findall(r'text="([^"]+)"', D.dump_hierarchy()) if t.strip()]

def click_text(t, wait=4):
    e = D(text=t)
    if e.exists:
        e.click(); time.sleep(wait); return True
    return False

D.app_start("com.instagram.android"); time.sleep(6)
D.click(0.9, 0.905); time.sleep(5)          # profile tab
click_text("Options") or D(description="Options").click(); time.sleep(4)
click_text("Account type and tools")
# the "Your account" row opens the switch sheet
click_text("Account type") or click_text("Professional")
time.sleep(3)
if click_text("Switch account type"):
    time.sleep(2)
    click_text("Switch to personal account")
    time.sleep(2)
    # confirm dialog — click the clickable confirm
    for m in re.finditer(r'<node[^>]*>', D.dump_hierarchy()):
        if "Switch to personal account" in m.group(0) and 'clickable="true"' in m.group(0):
            b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', m.group(0))
            D.click((int(b.group(1))+int(b.group(3)))//2, (int(b.group(2))+int(b.group(4)))//2)
            break
    time.sleep(6)

t = texts()
print("PERSONAL" if "Switch to professional account" in t else "still pro — check", t[:8])
sys.exit(0)
