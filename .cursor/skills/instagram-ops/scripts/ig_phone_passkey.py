#!/usr/bin/env python3
"""ig_phone_passkey.py — IG login via the phone's saved passkeys.

Drives the Instagram Android app on the Galaxy S22+ (wireless ADB) through
the saved-credential login lane — the trusted-device path that bypasses the
risk-scored web password-reset flow ("Something went wrong" on all desktop
automation lanes, 2026-11-22).

Flow: pre-flight status -> launch IG -> profile picker -> "Use another
profile" -> passkey/provider sheet -> tap provider -> report gate or verify.

The fingerprint/biometric prompt is a HUMAN GATE — the script detects it,
prints instructions, and polls until the user authenticates, then verifies
the feed actually loaded (picker-returning = dead saved session).

Usage:
  ig_phone_passkey.py                # full drive, waits for fingerprint
  ig_phone_passkey.py --no-wait      # stop at the gate, report only
  ig_phone_passkey.py --provider "Google Password Manager"
  ig_phone_passkey.py --package com.instagram.android

Requires the galaxy-s22-toolkit checkout at ~/galaxy-s22-toolkit
(phone-lib + tools/adb_connect.py). Exit 0 = feed verified, 1 = gate pending
or failure, 2 = phone blocked (cover/lock/other app focus).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

TOOLKIT = Path.home() / "galaxy-s22-toolkit"
sys.path.insert(0, str(TOOLKIT / "phone-lib" / "src"))
sys.path.insert(0, str(TOOLKIT / "tools"))

PICKER_MARKERS = ("use another profile", "create new account")
FEED_MARKERS = ("for you", "home", "search", "reels", "create")
GATE_MARKERS = ("fingerprint", "verify your identity", "confirm it's you",
                "screen lock", "biometric")


def sh(cmd: list[str], timeout: int = 30) -> str:
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout).stdout


def ui() -> "object":
    from adb_connect import load_wireless  # type: ignore
    from phone import create_client
    return create_client(wireless=load_wireless()).ui


def nodes(u) -> list:
    try:
        return u.dump() or []
    except Exception:
        return []


def text_of(u) -> str:
    return "\n".join(f"{n.text or ''} {n.desc or ''}" for n in nodes(u))


def tap_text(u, pattern: str, timeout: int = 8) -> bool:
    rx = re.compile(pattern, re.I)
    for n in nodes(u):
        if rx.search((n.text or "") + " " + (n.desc or "")) and n.bounds:
            x1, y1, x2, y2 = n.bounds
            u.tap((x1 + x2) // 2, (y1 + y2) // 2)
            return True
    return False


def status_blocked(u) -> str | None:
    st = u.status()
    if st.get("covered"):
        return "flip cover is CLOSED — open it and retry"
    if st.get("locked"):
        return "phone is LOCKED — unlock and retry"
    focus = st.get("activity") or ""
    if focus and "instagram.android" not in focus and "launcher" not in focus:
        return f"another app holds focus: {focus} — close it first"
    return None


def wait_state(u, want_feed: bool, timeout: int = 90) -> str:
    """Poll UI text; return 'feed' | 'picker' | 'gate' | 'timeout'."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        txt = text_of(u).lower()
        if any(m in txt for m in GATE_MARKERS):
            return "gate"
        if want_feed and any(m in txt for m in FEED_MARKERS) \
                and "log in" not in txt and "sign up" not in txt:
            return "feed"
        if any(m in txt for m in PICKER_MARKERS):
            return "picker"
        time.sleep(3)
    return "timeout"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-wait", action="store_true",
                    help="report the gate and exit instead of polling")
    ap.add_argument("--provider", default=None,
                    help="passkey provider substring (default: first entry)")
    ap.add_argument("--package", default="com.instagram.android")
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args()

    u = ui()
    blocked = status_blocked(u)
    if blocked:
        print(f"BLOCKED: {blocked}")
        return 2

    # launch IG
    sh(["adb", "shell", "monkey", "-p", args.package,
        "-c", "android.intent.category.LAUNCHER", "1"])
    state = wait_state(u, want_feed=True, timeout=20)
    if state == "feed":
        print("already AUTHENTICATED — feed loaded")
        return 0
    if state != "picker":
        print(f"unexpected state after launch: {state}")
        return 1

    # picker -> credential form
    if not tap_text(u, "use another profile"):
        print("picker visible but 'Use another profile' not found")
        return 1
    time.sleep(5)
    txt = text_of(u)

    # passkey/provider sheet -> pick provider
    if re.search(r"saved passkey|saved sign-ins", txt, re.I):
        providers = [n for n in nodes(u)
                     if re.search(r"samsung pass|google password|"
                                  r"more saved sign-ins", (n.text or "")
                                  + (n.desc or ""), re.I)]
        print("providers found:", [ (n.text or n.desc or "")[:40]
                                   for n in providers ])
        target = None
        if args.provider:
            rx = re.compile(args.provider, re.I)
            target = next((n for n in providers
                           if rx.search((n.text or "") + (n.desc or ""))),
                          None)
        if target is None and providers:
            target = providers[0]
        if target is None:
            print("no provider entries — falling through to form check")
        else:
            x1, y1, x2, y2 = target.bounds
            u.tap((x1 + x2) // 2, (y1 + y2) // 2)
            print(f"tapped provider: {(target.text or target.desc or '')[:50]}")
            time.sleep(4)
    elif re.search(r"username|password|log in", txt, re.I):
        print("plain login form reached (no saved passkey) — "
              "credential entry is a human gate; stopping")
        return 1
    else:
        print("unrecognized screen after 'Use another profile'")
        return 1

    # post-tap: fingerprint gate or feed
    state = wait_state(u, want_feed=True, timeout=args.timeout)
    if state == "gate":
        print("AUTH GATE: biometric/identity prompt on screen — "
              "touch the fingerprint sensor now")
        if args.no_wait:
            return 1
        state = wait_state(u, want_feed=True, timeout=args.timeout)
    if state == "feed":
        print("SUCCESS: Instagram feed loaded — session authenticated")
        return 0
    if state == "picker":
        print("FAIL: returned to profile picker — saved session is dead")
        return 1
    print(f"inconclusive: {state}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
