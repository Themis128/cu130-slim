#!/usr/bin/env python3
"""ntfy_set_server.py — swap the ntfy Android app's default server via ADB.

    ntfy_set_server.py https://ntfy.cloudless.gr [--topic cloudless-alerts]

Verifies the new URL answers before touching the phone, drives the ntfy
settings UI to change Default server, then re-adds the topic subscription
via the ntfy:// deep link. Requires adb connected (wireless or USB).
"""
import json
import re
import subprocess
import sys
import time
import urllib.request

PKG = "io.heckel.ntfy"


def adb(*args: str, timeout: int = 30) -> str:
    r = subprocess.run(["adb", *args], capture_output=True, text=True,
                       timeout=timeout)
    return r.stdout


def check_adb() -> None:
    out = adb("devices")
    if "device" not in out.splitlines()[-1] if out.strip() else True:
        raise SystemExit("no adb device — check wireless ADB / phone-access")


def dump() -> str:
    return adb("exec-out", "uiautomator", "dump", "/dev/tty")


def texts(xml: str) -> list[str]:
    return re.findall(r'text="([^"]+)"', xml)


def tap_label(label: str, xml: str | None = None) -> bool:
    """Tap the on-screen element whose text contains `label` (case-insens)."""
    xml = xml or dump()
    pat = re.compile(
        rf'text="[^"]*{re.escape(label)}[^"]*"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        re.IGNORECASE)
    m = pat.search(xml)
    if not m:
        return False
    x, y = (int(m.group(1)) + int(m.group(3))) // 2, (int(m.group(2)) + int(m.group(4))) // 2
    adb("shell", "input", "tap", str(x), str(y))
    time.sleep(2)
    return True


def tap_desc(desc: str, xml: str | None = None) -> bool:
    xml = xml or dump()
    pat = re.compile(
        rf'content-desc="[^"]*{re.escape(desc)}[^"]*"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        re.IGNORECASE)
    m = pat.search(xml)
    if not m:
        return False
    x, y = (int(m.group(1)) + int(m.group(3))) // 2, (int(m.group(2)) + int(m.group(4))) // 2
    adb("shell", "input", "tap", str(x), str(y))
    time.sleep(2)
    return True


def type_text(text: str) -> None:
    """Set the focused field's text via the broadcast IME path, else keytype."""
    safe = text.replace(" ", "%s")
    adb("shell", "input", "text", safe)
    time.sleep(1)


def verify_url(url: str) -> None:
    try:
        req = urllib.request.Request(url.rstrip("/") + "/", method="GET")
        code = urllib.request.urlopen(req, timeout=10).status
        if code >= 500:
            raise SystemExit(f"new server {url} returned {code} — refusing to point the app at it")
    except Exception as e:
        raise SystemExit(f"new server {url} unreachable ({e}) — refusing")


def main() -> int:
    new_url = sys.argv[1] if len(sys.argv) > 1 else ""
    topic = "cloudless-alerts"
    for i, a in enumerate(sys.argv):
        if a == "--topic" and i + 1 < len(sys.argv):
            topic = sys.argv[i + 1]
    if not new_url.startswith(("http://", "https://")):
        raise SystemExit("usage: ntfy_set_server.py https://host [--topic name]")

    check_adb()
    verify_url(new_url)
    print(f"new server OK: {new_url}")

    # open the app, dismiss any error dialog (top-left X)
    adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")
    time.sleep(3)
    xml = dump()
    if "Connection error" in xml:
        tap_desc("Close") or adb("shell", "input", "tap", "79", "164")
        time.sleep(1)

    # More options (top-right ⋮) → Settings → Default server
    tap_desc("More options") or adb("shell", "input", "tap", "1024", "164")
    time.sleep(1)
    if not tap_label("Settings"):
        raise SystemExit("Settings entry not found — UI may differ on this build")
    time.sleep(2)

    if not tap_label("Default server") and not tap_label("Server"):
        raise SystemExit("Default-server row not found in settings")
    time.sleep(2)

    # clear + type the new base URL, confirm
    xml = dump()
    fields = re.findall(r'<node[^>]*class="android.widget.EditText"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
    if fields:
        x, y = (int(fields[0][0]) + int(fields[0][2])) // 2, (int(fields[0][1]) + int(fields[0][3])) // 2
        adb("shell", "input", "tap", str(x), str(y))
        time.sleep(1)
        adb("shell", "input", "keyevent", "--longpress", "29")  # select all fallback
    adb("shell", "input", "keyevent", "KEYCODE_MOVE_END")
    # select-all then type over
    adb("shell", "input", "keyevent", "KEYCODE_CTRL_LEFT", "KEYCODE_A")
    type_text(new_url)
    for ok in ("OK", "Save", "Done", "Confirm"):
        if tap_label(ok):
            break

    # re-add the topic subscription via the ntfy:// deep link
    host = new_url.split("://", 1)[1].rstrip("/")
    adb("shell", "am", "start", "-a", "android.intent.action.VIEW",
        "-d", f"ntfy://{host}/{topic}")
    time.sleep(3)
    tap_label("Subscribe") or tap_desc("Subscribe")

    # verify: screenshot text should no longer show the connection error
    time.sleep(3)
    xml = dump()
    if "Connection error" in texts_list(xml) if False else ("Connection error" in xml):
        print("WARN: connection error still visible — check the server URL manually")
        return 1
    print(f"done — default server set to {new_url}, re-added {topic}")
    return 0


def texts_list(xml: str) -> list[str]:
    return re.findall(r'text="([^"]+)"', xml)


if __name__ == "__main__":
    sys.exit(main())
