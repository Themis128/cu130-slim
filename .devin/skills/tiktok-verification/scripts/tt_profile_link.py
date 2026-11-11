#!/usr/bin/env python3
"""tt_profile_link.py — set the TikTok profile website link via ADB on the
Galaxy S22+ (RFCTA0AEKZP). The web editor has no Links field (verified
2026-11-11); the mobile app Edit profile → Links → Website is the only path.

Safety: only drives while the phone looks idle — requires the TikTok app to
reach a stable logged-in profile screen. Aborts a cycle (never fights the
user) if unexpected screens appear. Run:

    python3 tt_profile_link.py            # one attempt
    python3 tt_profile_link.py --watch    # retry every 5 min for 24h

Log: ~/logs/tt-profile-link.log ; UI dumps: ~/logs/tt-dumps/
"""
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ADB = "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe"
SER = "RFCTA0AEKZP"
PKG = "com.zhiliaoapp.musically"
URL = "https://cloudless.gr/links"
LOG = Path.home() / "logs/tt-profile-link.log"
DUMPS = Path.home() / "logs/tt-dumps"
STATE = Path.home() / ".local/state/tt-profile-link.done"
LAUNCH_ACTIVITY = "com.ss.android.ugc.aweme.splash.SplashActivity"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def _serial() -> str:
    out = subprocess.run([ADB, "devices"], capture_output=True, text=True).stdout
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 2 and parts[1] == "device":
            if parts[0].startswith("RFCTA"):
                return parts[0]
            return parts[0]
    return SER


def adb(*args: str, timeout: int = 20) -> str:
    cmd = [ADB, "-s", _serial(), *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout


def dump(tag: str) -> str:
    DUMPS.mkdir(parents=True, exist_ok=True)
    adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    xml = adb("shell", "cat", "/sdcard/ui.xml")
    (DUMPS / f"{int(time.time())}-{tag}.xml").write_text(xml)
    return xml


def nodes(xml: str):
    """yield (text, desc, bounds) for each node."""
    for m in re.finditer(r"<node[^>]*>", xml):
        n = m.group(0)
        t = re.search(r'text="([^"]*)"', n)
        c = re.search(r'content-desc="([^"]*)"', n)
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
        if b:
            yield (t.group(1) if t else "", c.group(1) if c else "", b.groups())


def find(xml: str, *needles: str):
    """first node whose text+desc contains all needles (case-insensitive)."""
    for text, desc, b in nodes(xml):
        s = (text + " " + desc).lower()
        if all(nd.lower() in s for nd in needles):
            x1, y1, x2, y2 = map(int, b)
            return ((x1 + x2) // 2, (y1 + y2) // 2)
    return None


def tap(x: int, y: int) -> None:
    adb("shell", "input", "tap", str(x), str(y))
    time.sleep(2.5)


def has_any(xml: str, *needles: str) -> bool:
    return any(
        all(nd.lower() in (t + " " + c).lower() for nd in needles)
        for t, c, _ in nodes(xml)
    )


def user_active(xml: str) -> bool:
    """Heuristic: unexpected foreground apps / dialogs mean hands off."""
    bad = ["tailscale", "not now", "turn on", "sign in", "log in", "verify"]
    low = xml.lower()
    # only flag when these appear as foreground UI, not inside tiktok strings
    return any(b in low for b in bad[:2]) or has_any(xml, "create a new account")


def attempt() -> str:
    if STATE.exists():
        return "done"
    xml = dump("pre")
    # bring tiktok forward
    adb("shell", "am", "start", "-n", f"{PKG}/{LAUNCH_ACTIVITY}")
    time.sleep(6)
    xml = dump("launch")
    if user_active(xml):
        return "user-active"
    # dismiss common promo/login sheets
    for _ in range(3):
        if has_any(xml, "not now"):
            p = find(xml, "not now")
            if p:
                tap(*p)
                xml = dump("dismiss")
                continue
        if has_any(xml, "watch tutorial") or has_any(xml, "skip"):
            p = find(xml, "skip") or find(xml, "not now")
            if p:
                tap(*p)
                xml = dump("dismiss2")
                continue
        break
    # must be on logged-in feed/profile: look for bottom nav Profile
    p = find(xml, "profile")
    feed_markers = ["for you", "following", "inbox", "home"]
    if not p or not any(m in xml.lower() for m in feed_markers):
        log(f"not on tiktok feed; screen texts: {sorted({t for t,_,_ in nodes(xml) if t})[:15]}")
        return "not-ready"
    # go to profile tab (bottom-right 'Profile')
    prof = None
    for text, desc, b in nodes(xml):
        if (text or desc).strip().lower() == "profile":
            x1, y1, x2, y2 = map(int, b)
            if y1 > 2000:  # bottom nav only
                prof = ((x1 + x2) // 2, (y1 + y2) // 2)
    if not prof:
        return "no-profile-tab"
    tap(*prof)
    xml = dump("profile")
    # find Edit profile (button may be text or icon+label)
    ep = find(xml, "edit profile") or find(xml, "edit")
    if not ep:
        log("no Edit profile on page; texts: " + str(sorted({t for t,_,_ in nodes(xml) if t})[:25]))
        return "no-edit-button"
    tap(*ep)
    xml = dump("editor")
    # Links section: look for 'Links'/'Add'/'Website'
    for needles in [("links",), ("add",), ("website",)]:
        q = find(xml, *needles)
        if q:
            tap(*q)
            xml = dump("links")
            break
    w = find(xml, "website") or find(xml, "add")
    if not w:
        log("no website/add field; texts: " + str(sorted({t for t,_,_ in nodes(xml) if t})[:25]))
        return "no-website-field"
    tap(*w)
    xml = dump("website-form")
    # type the URL into the focused field
    adb("shell", "input", "text", URL.replace(":", "%3A").replace("/", "%2F"))
    time.sleep(1.5)
    xml = dump("typed")
    sv = find(xml, "save")
    if not sv:
        # try keyboard done / save variants
        sv = find(xml, "done") or find(xml, "confirm")
    if sv:
        tap(*sv)
        time.sleep(3)
        xml = dump("saved")
        STATE.touch()
        log("SUCCESS: website link submitted")
        return "success"
    log("no save button after typing")
    return "no-save"


def main() -> None:
    watch = "--watch" in sys.argv
    deadline = time.time() + (24 * 3600 if watch else 0)
    while True:
        try:
            res = attempt()
        except Exception as e:  # noqa: BLE001
            res = f"error:{e}"
        log(f"attempt -> {res}")
        if res in ("success", "done"):
            return
        if not watch or time.time() > deadline:
            return
        time.sleep(300)


if __name__ == "__main__":
    main()
