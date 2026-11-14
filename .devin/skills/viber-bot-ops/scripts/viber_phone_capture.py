#!/usr/bin/env python3
"""viber_phone_capture.py — pull the Viber bot auth token off the phone app
via ADB UI automation, then provision it.

PREREQUISITE: the account must actually HAVE a bot. Verified 2026-11-12:
this account has none — new bots are commercial-only (Rakuten Viber partner
approval) since 2024-02 and partners.viber.com is retired. This script is
the automated capture path for the day a bot exists; run it only after
bot creation, else it correctly reports the commercial-only blocker.

Flow: pick device -> wake -> launch Viber -> search bot name -> open the
bot chat -> open Edit Info -> locate the App Token -> copy via clipboard
-> read clipboard over Termux SSH -> validate -> viber_provision.py.

    viber_phone_capture.py run [--bot-name cloudless]
    viber_phone_capture.py devices
    viber_phone_capture.py probe    # just report app/screen state

Env: VIBER_ADB_SERIAL pins the device (default: prefer the phone — LAN
192.168.x or Tailscale 100.x — over other ADB endpoints like cameras).
Wireless-debugging ports are dynamic: when no device is connected,
pick_device() runs `adb mdns services`, connects to the phone's
_adb-tls-connect endpoint, and retries.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ADB_CANDIDATES = [
    "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe",
    "adb",
]
TERMUX_SSH = ("ssh -p 8022 -o ConnectTimeout=8 "
              "-o StrictHostKeyChecking=no u0_a357@192.168.1.77")
PROVISION = ("/home/tbaltzakis/cu130-slim/.devin/skills/viber-ops/scripts/"
             "viber_provision.py")
# Viber auth tokens look like UUIDs or long hex; be permissive then verify.
TOKEN_RE = re.compile(r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                      r"[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32,64})\b", re.I)

_adb_bin = None
_dev = None


def adb_bin():
    global _adb_bin
    if _adb_bin:
        return _adb_bin
    for c in ADB_CANDIDATES:
        try:
            subprocess.run([c, "version"], capture_output=True, timeout=10)
            _adb_bin = c
            return c
        except Exception:
            continue
    _adb_bin = "adb"
    return _adb_bin


def _adb(*args, timeout=25):
    return subprocess.run([adb_bin()] + list(args), capture_output=True,
                          text=True, timeout=timeout)


def devices():
    out = _adb("devices").stdout
    devs = [l.split()[0] for l in out.splitlines()[1:]
            if l.strip().endswith("device")]
    return devs


def discover_adb():
    """Wireless ADB uses a random port per pairing — find it via mDNS.

    `adb mdns services` lists `_adb-tls-connect._tcp` endpoints as
    `ip:port`; connect to each until one answers `device`. Returns the
    connected serial or None.
    """
    try:
        out = _adb("mdns", "services", timeout=10).stdout
    except Exception:
        return None
    for line in out.splitlines():
        m = re.search(r"(\d+\.\d+\.\d+\.\d+):(\d+)", line)
        if not m:
            continue
        ep = f"{m.group(1)}:{m.group(2)}"
        _adb("connect", ep, timeout=8)
        if ep in devices():
            return ep
    return None


def pick_device():
    global _dev
    if _dev:
        return _dev
    pinned = os.environ.get("VIBER_ADB_SERIAL")
    devs = devices()
    if not devs:
        found = discover_adb()
        if found:
            devs = devices()
    if pinned and pinned in devs:
        _dev = pinned
    elif devs:
        # prefer the phone (LAN 192.168.x or Tailscale 100.x), avoid
        # camera/other ADB endpoints
        phone = [d for d in devs
                 if d.split(":")[0].startswith(("192.168.", "100."))]
        _dev = phone[0] if phone else devs[0]
    return _dev


def sh(*args, timeout=25):
    dev = pick_device()
    if not dev:
        raise RuntimeError("no ADB device")
    return _adb("-s", dev, "shell", *args, timeout=timeout)


def dump_xml():
    """Return (xml, [(text, cx, cy), ...]) for the current screen."""
    sh("uiautomator", "dump", "/sdcard/vb_ui.xml")
    xml = sh("cat", "/sdcard/vb_ui.xml").stdout
    nodes = []
    for m in re.finditer(
            r'(?:text|content-desc)="([^"]*)"[^>]*'
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
        t, x1, y1, x2, y2 = m.groups()
        if t.strip():
            nodes.append((t, (int(x1) + int(x2)) // 2,
                          (int(y1) + int(y2)) // 2))
    return xml, nodes


def texts():
    return [t for t, _, _ in dump_xml()[1]]


def tap_text(needle, timeout=6):
    """Tap the first node whose text/desc contains needle (case-insens)."""
    needle_l = needle.lower()
    deadline = time.time() + timeout
    while time.time() < deadline:
        for t, x, y in dump_xml()[1]:
            if needle_l in t.lower():
                sh("input", "tap", str(x), str(y))
                time.sleep(1.5)
                return t
        time.sleep(1.2)
    return None


def type_text(text):
    """Clipboard + KEYCODE_PASTE — reliable on Samsung keyboards.

    Clipboard transport is Termux SSH; when that's unreachable (no creds),
    fall back to `input text` (ASCII-safe only — caller should keep typed
    values URL/ASCII).
    """
    r = subprocess.run(f"{TERMUX_SSH} \"termux-clipboard-set '{text}'\"",
                       shell=True, capture_output=True, timeout=15)
    if r.returncode == 0:
        sh("input", "keyevent", "KEYCODE_PASTE")
    else:
        # `input text` is ASCII-only; %s = space, % needs %25
        safe = (text.replace("%", "%25").replace(" ", "%s")
                .encode("ascii", "ignore").decode())
        sh("input", "text", safe)
    time.sleep(0.8)


def clipboard_get():
    r = subprocess.run(f'{TERMUX_SSH} "termux-clipboard-get"',
                       shell=True, capture_output=True, text=True,
                       timeout=15)
    return r.stdout.strip()


def focus_pkg():
    out = sh("dumpsys", "window").stdout
    m = re.search(r"mCurrentFocus=.*?([\w.]+)/", out)
    return m.group(1) if m else "?"


def launch_viber():
    sh("input", "keyevent", "KEYCODE_WAKEUP")
    time.sleep(1)
    # launcher intent — WelcomeActivity name varies by build
    r = sh("monkey", "-p", "com.viber.voip",
          "-c", "android.intent.category.LAUNCHER", "1")
    if "Events injected" not in (r.stdout + r.stderr):
        # fallback: explicit activity from pkg resolve
        sh("cmd", "package", "resolve-activity", "--brief",
           "com.viber.voip")
    time.sleep(5)


def cmd_devices(_args):
    devs = devices()
    for d in devs:
        print(d, "<- selected" if d == pick_device() else "")
    if not devs:
        print("no ADB devices — enable wireless debugging / plug USB")
        return 2
    return 0


def cmd_probe(_args):
    if not pick_device():
        print("no device")
        return 2
    print("device:", _dev)
    print("focus:", focus_pkg())
    for t in texts()[:25]:
        print("  •", t[:80])
    return 0


def _find_token_in_texts(ts):
    for t in ts:
        m = TOKEN_RE.search(t)
        if m:
            return m.group(1)
    return None


def cmd_run(args):
    if not pick_device():
        print("no ADB device — plug the phone / enable wireless ADB")
        return 2
    print("device:", _dev)

    launch_viber()
    print("focus:", focus_pkg())

    bot = args.bot_name
    # 1) open search (Viber's search affordance is a magnifier top-right)
    if not tap_text("search", timeout=4):
        # fall back to the known top-right search icon coords (S22+)
        sh("input", "tap", "1012", "152")
        time.sleep(2)
    # 2) type the bot name and submit
    type_text(bot)
    time.sleep(1)
    sh("input", "keyevent", "KEYCODE_ENTER")
    time.sleep(4)

    ts = texts()
    print("search results:", [t[:40] for t in ts[:15]])
    if not any(bot.lower() in t.lower() or "bot" in t.lower()
               for t in ts):
        print("BLOCKER: no bot named", repr(bot),
              "— this account has no Viber bot (commercial-only creation "
              "since 2024-02). Capture path ready for when one exists.")
        return 3

    # 3) open the bot result/chat
    if not tap_text(bot, timeout=5):
        tap_text("bot", timeout=3)
    time.sleep(3)

    # 4) open Edit Info — via the chat header name or the ⋮ overflow
    for label in ("edit info", "view info", "bot info", "about"):
        if tap_text(label, timeout=3):
            break
    else:
        # try the overflow menu then its info item
        sh("input", "tap", "980", "160")
        time.sleep(1.5)
        tap_text("info", timeout=3) or tap_text("edit", timeout=3)
    time.sleep(3)

    # 5) harvest the token — visible text first, then copy+clipboard
    token = _find_token_in_texts(texts())
    if not token:
        # tap the row labelled App token / Authentication token, then copy
        for label in ("app token", "authentication token", "token"):
            if tap_text(label, timeout=3):
                break
        time.sleep(1)
        tap_text("copy", timeout=3)
        time.sleep(1.5)
        clip = clipboard_get()
        m = TOKEN_RE.search(clip or "")
        token = m.group(1) if m else None
    if not token:
        print("no token found on Edit Info screen — dump for manual check")
        cmd_probe(args)
        return 4

    print("token captured:", token[:12] + "…")
    r = subprocess.run([sys.executable, PROVISION,
                        "--token", token, "--name", bot],
                       capture_output=True, text=True, timeout=90)
    print("provision:", "OK" if r.returncode == 0 else "FAILED")
    if r.stdout:
        print(r.stdout.strip()[:400])
    if r.returncode != 0 and r.stderr:
        print(r.stderr.strip()[:300])
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["run", "devices", "probe"])
    ap.add_argument("--bot-name", default="cloudless")
    args = ap.parse_args()
    return {"run": cmd_run, "devices": cmd_devices,
            "probe": cmd_probe}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
