#!/usr/bin/env python3
"""phone_type.py — IME-safe EditText entry on the Galaxy S22+.

The Samsung keyboard's suggestion layer appends junk ('v' and friends)
to `input text`, and backspaces fight the field's React-style control.
Proven-safe sequence (2026-10-30):
  1. tap the field
  2. tap the field's own ✕ clear button (NOT backspaces)
  3. `input text` the value
  4. verify the field text — strip a trailing junk char with ONE DEL
  5. hide the keyboard (BACK) before tapping the submit button —
     the commit of a pending suggestion is what re-appends chars.

Usage: phone_type.py --device RFCTA0AEKZP --value 804910
       phone_type.py --clear-x 956 1214 --value CODE
"""
import argparse
import sys
import re
import subprocess
import time


def adb(dev, *a):
    return subprocess.run(["adb", "-s", dev, *a],
                          capture_output=True, text=True).stdout


def dump(dev):
    return adb(dev, "exec-out", "uiautomator", "dump", "/dev/tty")


def field_texts(dev):
    xml = dump(dev)
    return re.findall(r'class="[^"]*(?:EditText|AutoCompleteTextView|SearchView)'
                      r'[^"]*"[^>]*text="([^"]*)"', xml)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", "-s", default=None)
    ap.add_argument("--value", required=True)
    ap.add_argument("--clear-x", type=int, nargs=2, metavar=("X", "Y"),
                    help="field clear (✕) button coords — default auto-detect")
    a = ap.parse_args()

    dev = a.device
    if not dev:
        out = adb("", "devices")
        devs = [l.split()[0] for l in out.splitlines()[1:]
                if l.strip() and l.rstrip().endswith("device")]
        if not devs:
            raise SystemExit("no device")
        dev = devs[0]

    xml = dump(dev)
    # prefer the focused field (whatever React/Material class it uses),
    # else any editable-looking node
    m = (re.search(r'focused="true"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
         or re.search(r'class="[^"]*(?:EditText|AutoCompleteTextView|SearchView)'
                      r'[^"]*"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
         or re.search(r'editable="true"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml))
    if not m:
        raise SystemExit("no editable field on screen")
    adb(dev, "shell", "input", "tap",
        str((int(m[1]) + int(m[3])) // 2),
        str((int(m[2]) + int(m[4])) // 2))
    time.sleep(1)

    if a.clear_x:
        adb(dev, "shell", "input", "tap", *map(str, a.clear_x))
        time.sleep(1)
    # hard clear via select-all + del as fallback
    adb(dev, "shell", "input", "keyevent", "KEYCODE_MOVE_END")
    for _ in range(3):
        adb(dev, "shell", "input", "keyevent", "KEYCODE_DEL")

    adb(dev, "shell", "input", "text", a.value)
    time.sleep(1)

    ts = field_texts(dev)
    cur = ts[0] if ts else ""
    if cur and cur != a.value and cur.startswith(a.value):
        adb(dev, "shell", "input", "keyevent", "KEYCODE_DEL")
        time.sleep(.5)
        ts = field_texts(dev)
        cur = ts[0] if ts else ""

    adb(dev, "shell", "input", "keyevent", "KEYCODE_BACK")  # hide kbd
    print("field:", repr(cur))
    sys.exit(0 if cur == a.value else 1)


if __name__ == "__main__":
    main()
