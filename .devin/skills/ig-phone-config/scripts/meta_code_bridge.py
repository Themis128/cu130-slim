#!/usr/bin/env python3
"""Meta/IG/Threads/WhatsApp code bridge for the Galaxy S22+ over ADB.

Reads the newest platform verification code straight out of the Android
notification buffer (`dumpsys notification --noredact`) and can fill a
focused code field on a checkpoint screen. No shade-opening needed.

Usage:
  meta_code_bridge.py            # print newest Meta-family code + focused app
  meta_code_bridge.py --all      # list every code-looking notification
  meta_code_bridge.py --fill     # fill the focused code field with newest code
  meta_code_bridge.py --watch S  # poll every S seconds, print on change

Codes expire fast (IG ~10 min) — prefer --fill promptly or pass a code
explicitly:  meta_code_bridge.py --fill 123456
"""
import re
import subprocess
import sys
import time

SERIAL = "192.168.1.77:5555"
CODE_RE = re.compile(r"\b(\d{6,8})\b")
# "316068 is your Instagram code" / "is your Facebook code" / WhatsApp variants
PLATFORM_RE = re.compile(
    r"(\d{6,8})\s+is your (Instagram|Facebook|WhatsApp|Meta|Threads)[^\n]*code",
    re.IGNORECASE,
)
# "use this code in the app: 85519490" (IG "Log back into Instagram" notif)
APP_CODE_RE = re.compile(
    r"(?:code in the app|security code is|confirmation code is)[:\s]+(\d{6,8})",
    re.IGNORECASE,
)


def adb(*args: str, timeout: int = 15) -> str:
    return subprocess.run(
        ["adb", "-s", SERIAL, *args],
        capture_output=True, text=True, timeout=timeout,
    ).stdout


def notifications() -> str:
    return adb("shell", "dumpsys", "notification", "--noredact")


def codes() -> list[tuple[str, str]]:
    """Return [(code, platform)] newest-first as they appear in the dump."""
    out = []
    for m in PLATFORM_RE.finditer(notifications()):
        out.append((m.group(1), m.group(2).lower()))
    for m in APP_CODE_RE.finditer(notifications()):
        out.append((m.group(1), "instagram-app"))
    # dedupe preserving order
    seen, dedup = set(), []
    for c in out:
        if c[0] not in seen:
            seen.add(c[0])
            dedup.append(c)
    return dedup


def newest_code() -> tuple[str, str] | None:
    c = codes()
    return c[0] if c else None


def focused_app() -> str:
    m = re.search(r"mCurrentFocus=Window\{[^ ]* ([^/}]+)", adb("shell", "dumpsys", "window"))
    return m.group(1) if m else "?"


def ui_xml() -> str:
    adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    return adb("shell", "cat", "/sdcard/ui.xml")


def code_field_center(xml: str) -> tuple[int, int] | None:
    """Center of a focused EditText on a code/checkpoint screen."""
    for node in re.findall(r"<node[^>]*>", xml):
        if 'class="android.widget.EditText"' in node and 'focused="true"' in node:
            m = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
            if m:
                x1, y1, x2, y2 = map(int, m.groups())
                return ((x1 + x2) // 2, (y1 + y2) // 2)
    return None


def fill(code: str) -> bool:
    xml = ui_xml()
    # Only act on screens that look like a code entry
    if not re.search(r"code|verification|bestätigung|κωδικός", xml, re.IGNORECASE):
        print("screen doesn't look like a code prompt — refusing to type")
        return False
    ctr = code_field_center(xml)
    if ctr:
        adb("shell", "input", "tap", str(ctr[0]), str(ctr[1]))
        time.sleep(0.5)
    adb("shell", "input", "text", code)
    print(f"typed {code} into focused field")
    return True


def main() -> None:
    args = sys.argv[1:]
    if "--all" in args:
        for code, plat in codes():
            print(f"{code}  ({plat})")
        return
    if "--watch" in args:
        i = args.index("--watch")
        interval = int(args[i + 1]) if i + 1 < len(args) else 15
        last = None
        while True:
            cur = newest_code()
            if cur != last:
                print(f"{time.strftime('%H:%M:%S')} app={focused_app()} code={cur}", flush=True)
                last = cur
            time.sleep(interval)
    if "--fill" in args:
        i = args.index("--fill")
        explicit = args[i + 1] if i + 1 < len(args) and args[i + 1].isdigit() else None
        code = explicit or (newest_code() or (None, None))[0]
        if not code:
            print("no Meta code found in notifications")
            sys.exit(1)
        sys.exit(0 if fill(code) else 1)
    # default: status
    print("focused:", focused_app())
    for code, plat in codes()[:5]:
        print(f"code: {code} ({plat})")


if __name__ == "__main__":
    main()
