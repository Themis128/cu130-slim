#!/usr/bin/env python3
"""phone_fb_approve.py — approve Facebook "Someone's trying to log in"
prompts that land on the PHONE's Facebook app while the server-sidecar
facebook-browser-sidecar (:9226) is logged OUT and can't see them.

The sidecar heal (ig_fb_session_heal.py) reaches Meta's 2FA gate; Meta
then pushes an approve_from_another_device notification to the phone's
logged-in FB app. This script closes the loop fully on-device:

  1. dumpsys notification -> find FB approve_from_another_device records
     (approval_id + post time; only act on <MAX_AGE_S old)
  2. am start fb://approve_from_another_device/open?approval_id=...&use_case=two_factor
  3. uiautomator dump -> verify the approval dialog is showing
     ("is this you trying to log in") and read the device/location text
  4. allowlist gate: approve ONLY device strings matching our infra
     (linux/wsl/windows/chrome/edge from the sidecar logins); anything
     else -> refuse + TG alert (possible account compromise)
  5. tap "Yes, it's me" via its clickable bounds — NO OCR
  6. dedupe by approval_id (state file), TG #ops alert on approve/refuse

Usage: phone_fb_approve.py [--once]   (cron every 3 min)
Env:   ADB (default platform-tools path), ADB_SERIAL (default USB serial)
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ADB = os.environ.get(
    "ADB",
    "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe",
)
SER = os.environ.get("ADB_SERIAL", "RFCTA0AEKZP")
STATE = os.path.expanduser("~/.local/state/phone-fb-approve.json")
MAX_AGE_S = 900          # ignore approvals older than 15 min
ALLOW_RE = re.compile(r"linux|wsl|windows|chrome|edge|docker", re.I)
DENY_HINT_RE = re.compile(r"unrecognized|new device|suspicious", re.I)
TG = os.path.expanduser("~/cu130-slim/.devin/skills/telegram-alert-ops/scripts/tg_alert.py")


def sh(*args, timeout=30):
    return subprocess.run(
        [ADB, "-s", SER, *args], capture_output=True, text=True, timeout=timeout
    ).stdout


def tg(text):
    try:
        subprocess.run(
            ["python3", TG, "ops", f"phone-fb-approve: {text}"],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"seen": {}}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    # keep only last 50 approval ids
    st["seen"] = dict(list(st.get("seen", {}).items())[-50:])
    json.dump(st, open(STATE, "w"))


def find_approvals():
    """Return [(approval_id, epoch)] for FB approve_from_another_device
    notifications in the shade, newest first."""
    out = sh("shell", "dumpsys notification --noredact", timeout=45)
    found = {}
    # approval ids appear inside the jns params blob near the notification
    for m in re.finditer(r"approval_id=(\d+)&use_case=two_factor", out):
        aid = m.group(1)
        # post time: nearest preceding '"time":<epoch>' inside same record
        seg = out[max(0, m.start() - 3000):m.start()]
        ts = re.findall(r'"time":(\d{10})', seg)
        found[aid] = int(ts[-1]) if ts else int(time.time())
    return sorted(found.items(), key=lambda kv: -kv[1])


TRANSIENT = {"no-dialog", "no-card", "no-tab", "no-button"}


def dump_ui():
    try:
        sh("shell", "uiautomator dump", timeout=45)
        return sh("shell", "cat /sdcard/window_dump.xml", timeout=30)
    except Exception:
        return ""


def node_bounds(xml, pattern):
    """Find clickable node whose subtree text matches pattern; return center."""
    for m in re.finditer(r"<node[^>]*clickable=\"true\"[^>]*>", xml):
        node = m.group(0)
        # text may be in child nodes — check a window around the node
        window = xml[m.start():m.start() + 3000]
        if not re.search(pattern, window, re.I | re.S):
            continue
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
        if b:
            x1, y1, x2, y2 = map(int, b.groups())
            return (x1 + x2) // 2, (y1 + y2) // 2
    return None


def device_text(xml):
    m = re.search(r"Login attempt from ([^\n<\"]{3,80})", xml)
    if m:
        return m.group(1)
    # Mobile-web pushes phrase the device differently (or omit the
    # "Login attempt from" prefix). Grab the text nodes surrounding the
    # approval phrasing — the allowlist regex then searches the card
    # copy itself for linux|wsl|windows|chrome|edge|docker.
    texts = re.findall(r'(?:text|content-desc)="([^"]{2,120})"', xml)
    for i, t in enumerate(texts):
        if re.search(r"trying to log|log ?in attempt|was this you|"
                     r"approve.*log|new login", t, re.I):
            return " | ".join(texts[max(0, i - 2):i + 4])
    return ""


def approve(aid):
    # The fb:// deep link is flaky about foregrounding the dialog — retry
    # twice with a HOME press between attempts (phone often sits on
    # recents/another app when cron fires).
    xml = ""
    for attempt in range(3):
        if attempt:
            sh("shell", "input", "keyevent", "KEYCODE_HOME", timeout=15)
            time.sleep(2)
        sh("shell", "am", "start", "-a", "android.intent.action.VIEW",
           "-d", f"fb://approve_from_another_device/open?approval_id={aid}&use_case=two_factor",
           "com.facebook.katana", timeout=20)
        time.sleep(6)
        xml = dump_ui()
        if "trying to log in" in xml.lower():
            break
    if "trying to log in" not in xml.lower():
        # Fallback: pull the shade and tap the FB approval notification
        # body — its contentIntent opens the same approval surface.
        sh("shell", "input", "keyevent", "KEYCODE_HOME", timeout=15)
        time.sleep(1)
        sh("shell", "input", "swipe", "540", "0", "540", "900", "300",
           timeout=15)
        time.sleep(3)
        xml = dump_ui()
        m = re.search(
            r'text="[^"]*trying to log in[^"]*"[^>]*'
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            sh("shell", "input", "tap", str((x1 + x2) // 2),
               str((y1 + y2) // 2), timeout=15)
            time.sleep(6)
            xml = dump_ui()
    if "trying to log in" not in xml.lower():
        # Third lane: FB app -> Notifications tab -> the approval card
        # has native buttons and doesn't depend on the flaky deep link.
        return approve_via_fb_tab(aid)
    dev = device_text(xml)
    if DENY_HINT_RE.search(dev):
        return "deny-hint", dev
    if not ALLOW_RE.search(dev):
        return "not-allowlisted", dev
    xy = node_bounds(xml, r"Yes, it")
    if not xy:
        # fallback: text node bounds
        m = re.search(r'text="Yes, it[^"]*"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            xy = ((x1 + x2) // 2, (y1 + y2) // 2)
    if not xy:
        return "no-button", dev
    sh("shell", "input", "tap", str(xy[0]), str(xy[1]), timeout=15)
    return "approved", dev


APPROVE_PAT = re.compile(
    r"\b(approve|yes, it|this was me|it was me|that's me)\b", re.I)


def _node_attr(node, name):
    m = re.search(name + r'="([^"]*)"', node)
    return m.group(1) if m else ""


def _clickable_xy(xml, pred):
    """Center coords of the first clickable node whose OWN text or
    content-desc satisfies pred(attr_text). Window matching is wrong —
    adjacent bottom-nav tabs bleed into each other's 1200-char windows."""
    for m in re.finditer(r'<node[^>]*clickable="true"[^>]*>', xml):
        node = m.group(0)
        label = _node_attr(node, "text") + " " + _node_attr(node, "content-desc")
        if not pred(label):
            continue
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
        if b:
            x1, y1, x2, y2 = map(int, b.groups())
            return (x1 + x2) // 2, (y1 + y2) // 2
    return None


def approve_via_fb_tab(aid):
    """Lane 3: launch the FB app, open its Notifications tab, and tap the
    approve control on the 'trying to log in' card. Requires an
    allowlisted device string in the dump — no device text means refuse
    (the deep-link dialog lane stays the verified path)."""
    sh("shell", "monkey", "-p", "com.facebook.katana",
       "-c", "android.intent.category.LAUNCHER", "1", timeout=20)
    time.sleep(5)
    xml = dump_ui()
    tab = _clickable_xy(
        xml, lambda w: re.search(r"notifications?,\s*tab", w, re.I))
    if not tab:
        # App may still be foregrounding or sitting on a stale activity —
        # one retry before declaring the lane dead.
        time.sleep(5)
        xml = dump_ui()
        tab = _clickable_xy(
            xml, lambda w: re.search(r"notifications?,\s*tab", w, re.I))

    if not tab:
        return "no-tab", ""
    sh("shell", "input", "tap", str(tab[0]), str(tab[1]), timeout=15)
    time.sleep(6)
    xml = dump_ui()
    # In-app card wording varies ("trying to log in", "login attempt",
    # "was this you?") — accept any login-approval phrasing.
    if not re.search(r"trying to log|log ?in attempt|was this you|"
                     r"approve.*log", xml, re.I):
        return "no-card", ""
    dev = device_text(xml)
    if not dev:
        # looser capture: 'Login attempt from X' may render as plain text
        m = re.search(r"(?:from|on)\s+([A-Z][A-Za-z0-9 ,.()_-]{3,60})",
                      xml)
        dev = m.group(1) if m else ""
    if DENY_HINT_RE.search(dev):
        return "deny-hint", dev
    # No device string -> cannot verify it's our infra -> refuse. The
    # deep-link dialog lane remains the primary verified path.
    if not dev or not ALLOW_RE.search(dev):
        return "not-allowlisted", dev
    # Find the positive control; never tap deny-ish labels.
    xy = None
    for m in re.finditer(r'<node[^>]*clickable="true"[^>]*>', xml):
        node = m.group(0)
        window = xml[m.start():m.start() + 1200]
        label = " ".join(re.findall(r'(?:text|content-desc)="([^"]*)"',
                                    window))
        if DENY_HINT_RE.search(label) or re.search(
                r"\b(deny|decline|not now|maybe later|wasn'?t me|no thanks)\b",
                label, re.I):
            continue
        if APPROVE_PAT.search(label):
            b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
            if b:
                x1, y1, x2, y2 = map(int, b.groups())
                xy = ((x1 + x2) // 2, (y1 + y2) // 2)
                break
    if not xy:
        return "no-button", dev
    sh("shell", "input", "tap", str(xy[0]), str(xy[1]), timeout=15)
    return "approved", dev


def visible_dialog_approve():
    """Fast path: the approval dialog can already be on screen (FB app
    foreground or system overlay) with NO shade notification record —
    find_approvals() then returns nothing and the prompt sits until it
    expires. Verify the device string against the allowlist and tap
    "Yes, it's me" directly. Returns None when no dialog is visible."""
    xml = dump_ui()
    if not xml:
        return None
    # Require the actual dialog affordance — the shade notification itself
    # contains "trying to log in" text but has no Yes/No buttons and no
    # parseable device string; matching it produced false refusals.
    if 'Yes, it' not in xml:
        return None
    dev = device_text(xml)
    if DENY_HINT_RE.search(dev):
        return "deny-hint", dev
    if not dev or not ALLOW_RE.search(dev):
        # No parseable device string -> cannot verify it's our infra.
        # Skip silently rather than refuse-alert: notification-shade
        # captures and partial renders land here too.
        return None
    xy = node_bounds(xml, r"Yes, it")
    if not xy:
        m = re.search(
            r'text="Yes, it[^"]*"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            xml)
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            xy = ((x1 + x2) // 2, (y1 + y2) // 2)
    if not xy:
        return "no-button", dev
    sh("shell", "input", "tap", str(xy[0]), str(xy[1]), timeout=15)
    return "approved", dev


def main():
    once = "--once" in sys.argv
    st = load_state()
    seen = st.setdefault("seen", {})
    while True:
        # Fast path first: an already-visible dialog has no notification
        # record, so find_approvals() alone can miss it entirely.
        try:
            vis = visible_dialog_approve()
        except Exception as e:
            print(f"visible-dialog check failed: {e}")
            vis = None
        if vis:
            res, dev = vis
            vkey = f"vis:{(dev or '?').strip()[:40]}:{int(time.time() // 3600)}"
            if res in TRANSIENT:
                print(f"visible dialog -> {res} dev='{dev[:50]}' (retry next cycle)")
            elif vkey not in seen:
                seen[vkey] = res
                save_state(st)
                if res == "approved":
                    tg(f"APPROVED FB login ({dev.strip()[:60]}) [visible-dialog]")
                elif res in ("not-allowlisted", "deny-hint"):
                    tg(f"REFUSED FB login approval — device '{dev.strip()[:60]}' not allowlisted [visible-dialog]")
                else:
                    print(f"visible dialog -> {res} dev='{dev[:50]}'")
        try:
            approvals = find_approvals()
        except Exception as e:
            print(f"dumpsys failed: {e}")
            approvals = []
        acted = False
        for aid, ts in approvals:
            if aid in seen or time.time() - ts > MAX_AGE_S:
                continue
            res, dev = approve(aid)
            acted = True
            if res in TRANSIENT:
                # transient UI misses — retry next cron cycle while the
                # approval is still fresh (MAX_AGE_S); don't dedupe
                print(f"aid={aid} -> {res} dev='{dev[:50]}' (retry next cycle)")
                continue
            seen[aid] = res
            if res == "approved":
                tg(f"APPROVED FB login ({dev.strip()[:60]}) aid={aid}")
            elif res in ("not-allowlisted", "deny-hint"):
                tg(f"REFUSED FB login approval — device '{dev.strip()[:60]}' not allowlisted aid={aid}")
            else:
                print(f"aid={aid} -> {res} dev='{dev[:50]}'")
            save_state(st)
        if once or not acted:
            break
        time.sleep(120)


if __name__ == "__main__":
    main()
