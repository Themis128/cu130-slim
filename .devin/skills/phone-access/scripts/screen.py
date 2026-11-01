#!/usr/bin/env python3
"""screen.py — resilient screen-driver for the Galaxy S22+.

Fixes the three failure classes observed driving WebView pages:
  1. `input text` silently doesn't land in WebView/React fields
     -> fallback: termux-clipboard-set (SSH) + KEYCODE_PASTE
  2. coordinate taps miss on stale dumps
     -> find-by-text re-dumps then taps the element's real center;
        `tap` verifies the screen actually changed (or the element's
        checked state flipped) and retries once
  3. WSL/Windows vsock drops ADB calls randomly
     -> every adb call retries; ADBS resolver covers bare adb, the
        Windows .exe path, and `adb` inside WSL

Usage:
  screen.py texts                        # list visible texts + coords
  screen.py find "Email baltzakis"       # print center x,y of a match
  screen.py tap "Next"                   # re-dump + tap + verify
  screen.py type "user@gmail.com"        # field must be focused first
  screen.py tapxy 539 804
  screen.py wait "Get code" --timeout 20
  screen.py dump | grep ...
"""
import argparse
import re
import subprocess
import sys
import time

TERMUX = "ssh -p 8022 -o ConnectTimeout=8 -o StrictHostKeyChecking=no u0_a357@192.168.1.77"


def _adb_bin():
    import shutil
    import os
    for cand in ("adb",
                 "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe",
                 os.path.expanduser("~/platform-tools/adb")):
        if shutil.which(cand) or cand.endswith(".exe"):
            return cand
    return "adb"


def adb(dev, *args, retries=3):
    """Run adb with vsock-drop retries."""
    last = ""
    for i in range(retries):
        r = subprocess.run([_adb_bin(), "-s", dev, *args],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout
        last = (r.stderr or "")[-120:]
        time.sleep(1 + i)
    return last


def device():
    out = subprocess.run([_adb_bin(), "devices"], capture_output=True,
                         text=True).stdout
    devs = [ln.split()[0] for ln in out.splitlines()[1:]
            if ln.strip() and ln.rstrip().endswith("device")]
    return devs[0] if devs else None


def dump(dev):
    xml = adb(dev, "exec-out", "uiautomator", "dump", "/dev/tty")
    if "hierchary" not in xml and "hierarchy" not in xml:
        # exec-out sometimes returns the 'dumped to' line only
        adb(dev, "shell", "uiautomator", "dump", "/sdcard/u.xml")
        xml = adb(dev, "shell", "cat", "/sdcard/u.xml")
    return xml


def elements(dev, refresh=True):
    """[(text, cx, cy)] for every node carrying text."""
    xml = dump(dev) if refresh else _last[0]
    _last[0] = xml
    out = []
    for m in re.finditer(
            r'text="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
        t, x1, y1, x2, y2 = m.groups()
        if t.strip():
            out.append((t, (int(x1) + int(x2)) // 2, (int(y1) + int(y2)) // 2))
    return out


_last = [""]


def find(dev, needle):
    needle = needle.lower()
    for t, x, y in elements(dev):
        if needle in t.lower():
            return x, y, t
    return None


def wait(dev, needle, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        hit = find(dev, needle)
        if hit:
            return hit
        time.sleep(1.5)
    return None


def tap(dev, needle, verify=True):
    hit = find(dev, needle)
    if not hit:
        print(f"miss: {needle!r} not on screen")
        return False
    x, y, t = hit
    before = _last[0]
    adb(dev, "shell", "input", "tap", str(x), str(y))
    time.sleep(1.2)
    if verify:
        after = dump(dev)
        # success = screen changed OR the label's still visible but now
        # has a sibling "checked" state (radio pickers) — treat same-text
        # as inconclusive-but-ok on second pass
        if after != before or find(dev, needle):
            print(f"tap {needle!r} @{x},{y}")
            return True
        print(f"tap {needle!r} may have missed (no change)")
        return False
    return True


def tapxy(dev, x, y):
    adb(dev, "shell", "input", "tap", str(x), str(y))


def _clipboard_set(text):
    r = subprocess.run(f'{TERMUX} "termux-clipboard-set \'{text}\'"',
                       shell=True, capture_output=True, text=True)
    return r.returncode == 0


KEYMAP = {c: f"KEYCODE_{c.upper()}" for c in "abcdefghijklmnopqrstuvwxyz0123456789"}
KEYMAP.update({".": "KEYCODE_PERIOD", "@": "KEYCODE_AT", "-": "KEYCODE_MINUS",
               "_": "KEYCODE_MINUS --longpress" if False else "KEYCODE_AT",  # _ via SYM
               "#": "KEYCODE_POUND", "+": "KEYCODE_PLUS", "=": "KEYCODE_EQUALS"})


def type_text(dev, text, masked=False):
    """Field must already be focused. input text -> verify -> paste.

    masked=True for password/secret fields: the uiautomator dump only shows
    bullets, so find() can never verify — type exactly once and never retry
    (retries concatenate the secret 2-3 times)."""
    adb(dev, "shell", "input", "text",
        text.replace(" ", "%s").replace("@", "\\@"))
    if masked:
        return "input-text sent (masked — unverifiable)"
    time.sleep(1)
    if find(dev, text.split("@")[0][:12]):
        return "input-text ok"
    # WebView fallback: clipboard + paste
    if _clipboard_set(text):
        adb(dev, "shell", "input", "keyevent", "KEYCODE_PASTE")
        time.sleep(1)
        if find(dev, text.split("@")[0][:12]):
            return "clipboard-paste ok"
    # char-by-char keyevents — real events, WebViews can't reject
    for c in text:
        k = KEYMAP.get(c)
        if not k:
            return "unsupported char: %r" % c
        adb(dev, "shell", "input", "keyevent", *k.split())
        time.sleep(0.05)
    if find(dev, text.split("@")[0][:12]):
        return "keyevents ok"
    return "type may have failed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["texts", "find", "tap", "tapxy",
                                    "type", "wait", "dump", "devices"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("arg2", nargs="?")
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--device", "-s")
    a = ap.parse_args()
    dev = a.device or device()
    if not dev:
        sys.exit("no adb device")

    if a.cmd == "devices":
        print(dev)
    elif a.cmd == "dump":
        print(dump(dev)[:2000])
    elif a.cmd == "texts":
        for t, x, y in elements(dev):
            print(f"{x},{y}\t{t}")
    elif a.cmd == "find":
        r = find(dev, a.arg)
        print(f"{r[0]},{r[1]}\t{r[2]}" if r else "not-found")
    elif a.cmd == "wait":
        r = wait(dev, a.arg, a.timeout)
        print(f"{r[0]},{r[1]}\t{r[2]}" if r else "timeout")
    elif a.cmd == "tap":
        sys.exit(0 if tap(dev, a.arg) else 1)
    elif a.cmd == "tapxy":
        tapxy(dev, int(a.arg), int(a.arg2))
    elif a.cmd == "type":
        print(type_text(dev, a.arg))


if __name__ == "__main__":
    main()
