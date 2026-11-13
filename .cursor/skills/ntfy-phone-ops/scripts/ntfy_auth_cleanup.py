#!/usr/bin/env python3
"""ntfy_auth_cleanup.py — remove dead server auth entries from the ntfy app.

The app accumulates one credential entry per server-string variant
(`ntfy.cloudless.gr` vs `ntfy.cloudless.gr/` vs tailnet name vs NodePort
IPs). Stale entries with outdated passwords produce
"User phone not authorized" on new subscriptions.

Verified UI flow (Galaxy S22+, ntfy 1.25.2, 2026-11-13):

  am start -n io.heckel.ntfy/.ui.MainActivity
    -> "More options" (top-right) -> "Settings" -> "Manage users"
    -> tap a user row -> "Edit user" -> "More options" -> "Delete user"

Only rows whose server string matches DEAD_HOSTS are touched; the
canonical ntfy.cloudless.gr entries are never deleted.

    python3 ntfy_auth_cleanup.py            # dry-run: list entries
    python3 ntfy_auth_cleanup.py --apply    # delete dead entries

Requires: adb (Windows SDK adb.exe via WSL interop works), phone
RFCTA0AEKZP authorized + connected.
"""
import re
import subprocess
import sys
import time

ADB_CANDIDATES = [
    "adb",
    "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe",
]
SERIAL = "RFCTA0AEKZP"
PKG = "io.heckel.ntfy"
ACTIVITY = "io.heckel.ntfy/.ui.MainActivity"

# Dead/duplicate server strings — canonical entries must NOT match.
DEAD_HOSTS = [
    "ntfy.tail4ecae1.ts.net",
    "100.74.191.58",
    "192.168.1.200",
]
CANONICAL = "ntfy.cloudless.gr"


def _adb() -> str:
    import shutil
    from pathlib import Path
    for c in ADB_CANDIDATES:
        if c == "adb" and shutil.which("adb"):
            return "adb"
        if c != "adb" and Path(c).exists():
            return c
    raise SystemExit("adb not found")


def sh(*args: str, timeout: int = 30) -> str:
    r = subprocess.run([_adb(), "-s", SERIAL, *args],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout or ""


def dump_ui() -> str:
    """uiautomator dump to /dev/tty — avoids MSYS path mangling."""
    out = sh("exec-out", "uiautomator", "dump", "/dev/tty", timeout=45)
    m = re.search(r'(<?xml[^>]*>.*</hierarchy>)', out, re.S)
    return m.group(1) if m else out


def node_xy(xml: str, needle: str) -> tuple[int, int] | None:
    """Center coords of the first node whose text/desc contains needle."""
    for m in re.finditer(r'<node[^>]*>', xml):
        node = m.group(0)
        attrs = " ".join(re.findall(
            r'(?:text|content-desc)="([^"]*)"', node))
        if needle.lower() not in attrs.lower():
            continue
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
        if b:
            x1, y1, x2, y2 = map(int, b.groups())
            return (x1 + x2) // 2, (y1 + y2) // 2
    return None


def click_row(xml: str, server: str) -> bool:
    """Tap the clickable row CONTAINING the server string.

    Rows are full-width clickable bands; the server text sits in the
    upper part of the band. Find the text's y, then tap the center of
    the clickable node whose vertical span contains it.
    """
    tm = None
    for m in re.finditer(r'<node[^>]*text="([^"]*)"[^>]*>', xml):
        if server in m.group(1):
            b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', m.group(0))
            if b:
                tm = tuple(map(int, b.groups()))
                break
    if not tm:
        return False
    ty = (tm[1] + tm[3]) // 2
    tx = (tm[0] + tm[2]) // 2
    for m in re.finditer(r'<node[^>]*clickable="true"[^>]*>', xml):
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', m.group(0))
        if not b:
            continue
        x1, y1, x2, y2 = map(int, b.groups())
        if y1 <= ty <= y2:
            sh("shell", "input", "tap", str((x1 + x2) // 2),
               str((y1 + y2) // 2))
            return True
    # fallback: tap the text itself
    sh("shell", "input", "tap", str(tx), str(ty))
    return True


def tap_text(xml: str, needle: str) -> bool:
    xy = node_xy(xml, needle)
    if not xy:
        return False
    sh("shell", "input", "tap", str(xy[0]), str(xy[1]))
    return True


def launch() -> None:
    sh("shell", "am", "start", "-n", ACTIVITY, timeout=20)
    time.sleep(4)
    for _ in range(3):
        focus = sh("shell", "dumpsys", "window", timeout=20)
        if PKG in focus:
            return
        time.sleep(2)
    raise SystemExit("ntfy app did not reach foreground — aborting (no blind taps)")


def open_manage_users() -> str:
    xml = dump_ui()
    for attempt in range(4):
        # Users list screen has "Add new user"; the Settings screen only
        # has the "Manage users" row — tap it to descend.
        if "Add new user" in xml:
            return xml
        if "Manage users" in xml:
            if tap_text(xml, "Manage users"):
                time.sleep(2)
                xml = dump_ui()
                continue
        mo = node_xy(xml, "More options")
        if mo:
            sh("shell", "input", "tap", str(mo[0]), str(mo[1]))
            time.sleep(2)
            xml = dump_ui()
            if tap_text(xml, "Settings"):
                time.sleep(2)
                xml = dump_ui()
                continue
        time.sleep(2)
        xml = dump_ui()
    if "Add new user" in xml:
        return xml
    raise SystemExit("could not reach Manage users screen")


def list_entries(xml: str) -> list[tuple[str, str]]:
    """Return [(server, usage)] pairs from the Manage users list."""
    texts = re.findall(r'text="([^"]+)"', xml)
    entries = []
    for i, t in enumerate(texts):
        tl = t.strip()
        if not tl or "://" in tl or tl in ("phone", "Manage users",
                                        "Add users", "Add new user",
                                        "Create a new user for a new server"):
            continue
        if any(h in tl for h in DEAD_HOSTS) or tl == CANONICAL \
                or tl == CANONICAL + "/":
            usage = texts[i + 2] if i + 2 < len(texts) else ""
            entries.append((tl, usage))
    return entries


def delete_entry(server: str) -> bool:
    """Row -> Edit user -> More options -> Delete user."""
    xml = dump_ui()
    if not click_row(xml, server):
        return False
    time.sleep(2)
    xml = dump_ui()
    if "Edit user" not in xml:
        return False
    mo = node_xy(xml, "More options")
    if not mo:
        return False
    sh("shell", "input", "tap", str(mo[0]), str(mo[1]))
    time.sleep(2)
    xml = dump_ui()
    if not tap_text(xml, "Delete user"):
        return False
    time.sleep(2)
    return True


def main() -> int:
    apply = "--apply" in sys.argv
    launch()
    xml = open_manage_users()
    entries = list_entries(xml)
    dead = [s for s, _ in entries if any(h in s for h in DEAD_HOSTS)]
    keep = [s for s, _ in entries if s not in dead]
    print(f"entries kept: {keep}")
    print(f"dead entries found: {dead}")
    if not dead:
        print("nothing to clean")
        return 0
    if not apply:
        print("dry-run — re-run with --apply to delete")
        return 0
    removed = []
    for server in dead:
        if delete_entry(server):
            removed.append(server)
            print(f"removed: {server}")
            time.sleep(1)
        else:
            print(f"FAILED to remove: {server}")
            # try to get back to the list
            sh("shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(1)
    print(f"done — removed {len(removed)}/{len(dead)}")
    return 0 if len(removed) == len(dead) else 1


if __name__ == "__main__":
    sys.exit(main())
