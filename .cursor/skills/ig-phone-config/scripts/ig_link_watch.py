#!/usr/bin/env python3
"""ig_link_watch.py — wait for the Galaxy S22+ cover to open, then set the
Instagram profile website link to https://cloudless.gr/links via the mobile app.

The IG website field is mobile-app-only (web POST silently drops external_url;
mobile API rejects web sessionids). The only blocker is the physical flip
cover: while CoverHomeActivity is focused, input lands nowhere. This watcher
polls the focused app every POLL_SECONDS and fires the UI flow the moment the
phone is unfolded/unlocked.

Usage:
    python3 ig_link_watch.py            # poll until open, then drive IG
    python3 ig_link_watch.py --once     # single cover check, exit
    python3 ig_link_watch.py --status   # print focused app, exit

Env:
    ADB_SERIAL   device serial (default RFCTA0AEKZP)
    ADB_BIN      adb binary path
    IG_LINK      URL to set (default https://cloudless.gr/links)
    POLL_SECONDS cover poll interval (default 30)
    TIMEOUT_H    give up after N hours (default 24)
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ADB_BIN = "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe"
SERIAL = "RFCTA0AEKZP"
LINK = "https://cloudless.gr/links"
POLL = 30
TIMEOUT_H = 24
LOG = Path.home() / "logs" / "ig-link-watch.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def adb(*args: str, timeout: int = 20) -> str:
    serial = os.environ.get("ADB_SERIAL", SERIAL)
    binpath = os.environ.get("ADB_BIN", ADB_BIN)
    cmd = [binpath, "-s", serial, *args]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return out.stdout + out.stderr


def focused_app() -> str:
    out = adb("shell", "dumpsys", "window", timeout=45)
    line = next((l for l in out.splitlines() if "mFocusedApp=" in l), "")
    m = re.search(r" u0 (\S+)", line)
    return m.group(1) if m else (line.strip()[:80] or "unknown")


def covered() -> bool:
    app = focused_app()
    # unknown/unreadable state → assume covered (never fire blind input)
    return app == "unknown" or "CoverHomeActivity" in app


def wake_unlock() -> None:
    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    time.sleep(1)
    adb("shell", "input", "keyevent", "82")  # MENU unlock
    time.sleep(1)
    adb("shell", "input", "swipe", "540", "2000", "540", "400", "300")
    time.sleep(2)


def ui_xml() -> str:
    adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    return adb("shell", "cat", "/sdcard/ui.xml")


def find_bounds(xml: str, *needles: str) -> tuple[int, int] | None:
    """Return center of first node whose text/content-desc contains a needle."""
    for node in re.findall(r"<node[^>]*?/?>", xml):
        hay = node
        for n in needles:
            if n.lower() in hay.lower():
                m = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
                if m:
                    l, t, r, b = map(int, m.groups())
                    return ((l + r) // 2, (t + b) // 2)
    return None


def tap(x: int, y: int) -> None:
    adb("shell", "input", "tap", str(x), str(y))
    time.sleep(1.5)


def tap_text(*needles: str, exact: bool = False) -> bool:
    xml = ui_xml()
    # exact-ish pass first: match text="needle" attribute exactly
    if exact:
        for node in re.findall(r"<node[^>]*?/?>", xml):
            for attr in ("text", "content-desc"):
                m = re.search(attr + r'="([^"]*)"', node)
                if m and m.group(1).strip().lower() in [n.lower() for n in needles]:
                    b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
                    if b:
                        l, t, r, bb = map(int, b.groups())
                        tap((l + r) // 2, (t + bb) // 2)
                        return True
    pt = find_bounds(xml, *needles)
    if pt:
        tap(*pt)
        return True
    return False


def type_text(s: str) -> None:
    adb("shell", "input", "text", s.replace(" ", "%s").replace("%", "%%"))
    time.sleep(1)


def visible_texts(limit: int = 60) -> list[str]:
    """Text/content-desc of visible nodes — for failure diagnosis."""
    xml = ui_xml()
    out = []
    for node in re.findall(r"<node[^>]*?/?>", xml):
        for attr in ("text", "content-desc"):
            m = re.search(attr + r'="([^"]*)"', node)
            if m and m.group(1).strip():
                out.append(m.group(1).strip()[:60])
                break
        if len(out) >= limit:
            break
    return out


def shot(name: str) -> None:
    try:
        out = subprocess.run(
            [os.environ.get("ADB_BIN", ADB_BIN), "-s", os.environ.get("ADB_SERIAL", SERIAL),
             "exec-out", "screencap", "-p"],
            capture_output=True, timeout=20,
        )
        p = Path.home() / "logs" / f"ig-link-{name}.png"
        p.write_bytes(out.stdout)
        log(f"screenshot -> {p}")
    except Exception as e:  # noqa: BLE001
        log(f"screenshot failed: {e}")


def drive_ig(link: str) -> bool:
    """Best-effort IG app navigation. Returns True if we reached a save action."""
    log("cover open — launching Instagram")
    adb("shell", "monkey", "-p", "com.instagram.android", "-c",
        "android.intent.category.LAUNCHER", "1")
    time.sleep(6)
    shot("01-launch")

    # Dismiss any popup/interstitial
    for _ in range(3):
        for dismiss in ("Not now", "Not Now", "Skip", "Maybe later", "Close", "Got it"):
            if tap_text(dismiss, exact=True):
                log(f"dismissed interstitial: {dismiss}")
                break
        else:
            break
        time.sleep(1)

    # Path A: profile tab → Edit profile → Links
    # Profile tab is bottom-right; find by content-desc "Profile"
    tapped_profile = False
    for _attempt in range(2):
        xml = ui_xml()
        pt = find_bounds(xml, 'content-desc="Profile"', 'Profile')
        if not pt:
            pt = find_bounds(xml, "Profile")
        if pt:
            tap(*pt)
            tapped_profile = True
            log("tapped profile tab")
            time.sleep(3)
        shot(f"02-profile{'-retry' if _attempt else ''}")
        # verify we're on own profile: look for Edit profile or username
        xml = ui_xml()
        if find_bounds(xml, "Edit profile", "Edit Profile", "cloudless.gr"):
            break
        log(f"profile not confirmed, visible: {visible_texts(15)}")
    if not tapped_profile:
        log(f"no Profile tab found, visible: {visible_texts(15)}")

    # Edit profile button (may need scroll on some layouts)
    if not tap_text("Edit profile", "Edit Profile"):
        adb("shell", "input", "swipe", "540", "1600", "540", "900", "300")
        time.sleep(1.5)
        if not tap_text("Edit profile", "Edit Profile"):
            log(f"no Edit profile button found, visible: {visible_texts(20)}")
            shot("03-noedit")
            return False
    time.sleep(3)
    shot("04-editprofile")

    # Links row (may need scroll — try direct first)
    if not tap_text("Links", "Links in bio", "Add external link"):
        # scroll down inside the form
        adb("shell", "input", "swipe", "540", "1800", "540", "700", "300")
        time.sleep(1.5)
        if not tap_text("Links", "Add external link"):
            log("no Links row found after scroll")
            shot("05-nolinks")
            return False
    log("opened Links")
    time.sleep(2)
    shot("06-links")

    # Add external link
    if tap_text("Add external link", "Add link", "Add URL", exact=False):
        log("tapped add link")
        time.sleep(2)
    else:
        # maybe a single existing link row to edit
        tap_text("Link", "Website")
        time.sleep(1.5)

    # URL field — tap it then replace text
    xml = ui_xml()
    # find editable field
    fields = re.findall(r'<node[^>]*class="android.widget.EditText"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*/?>', xml)
    if fields:
        l, t, r, b = map(int, fields[0])
        tap((l + r) // 2, (t + b) // 2)
        time.sleep(1)
        # select all + delete then type
        adb("shell", "input", "keyevent", "KEYCODE_MOVE_END")
        # clear existing content: cursor to end, then delete backwards
        for _ in range(60):
            adb("shell", "input", "keyevent", "KEYCODE_DEL")
        adb("shell", "input", "text", link)
        log(f"typed link into first EditText")
    else:
        log("no EditText found — manual step needed")
        shot("07-nofield")
        return False

    time.sleep(1)
    # Save / Done / Submit
    for label in ("Done", "Save", "Submit", "Add", "OK"):
        if tap_text(label, exact=True):
            log(f"tapped save: {label}")
            time.sleep(3)
            shot("08-saved")
            return True
    log("no save button found")
    shot("09-nosave")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--link", default=os.environ.get("IG_LINK", LINK))
    args = ap.parse_args()

    global POLL, TIMEOUT_H
    POLL = int(os.environ.get("POLL_SECONDS", POLL))
    TIMEOUT_H = float(os.environ.get("TIMEOUT_H", TIMEOUT_H))
    link = args.link

    if args.status or args.once:
        log(f"focused={focused_app()} covered={covered()}")
        if args.once and not covered():
            return drive_ig(link)
        return 0

    deadline = time.time() + TIMEOUT_H * 3600
    log(f"watching for cover open (timeout {TIMEOUT_H}h, poll {POLL}s, link={link})")
    while time.time() < deadline:
        try:
            if not covered():
                wake_unlock()
                time.sleep(2)
                if not covered():
                    for attempt in range(3):
                        ok = drive_ig(link)
                        log(f"drive attempt {attempt + 1} result: {'OK' if ok else 'INCOMPLETE'}")
                        if ok:
                            return 0
                        if covered():
                            break
                        time.sleep(3)
                    # still uncovered but failed — keep watching for next window
                    log("drive failed, resuming watch")
        except subprocess.TimeoutExpired:
            log("adb timeout — retrying")
        except Exception as e:  # noqa: BLE001
            log(f"error: {e}")
        time.sleep(POLL)
    log("timeout — cover never opened")
    return 3


if __name__ == "__main__":
    sys.exit(main())
