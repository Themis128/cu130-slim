#!/usr/bin/env python3
"""phone_gmail_otp.py — pull the newest OTP code from the phone's Gmail.

The Gmail app on the Galaxy S22+ (baltzakis.themis@gmail.com) renders OTP
subjects/snippets in two ADB-readable places — no root, no OCR:

  1. ``dumpsys notification --noredact`` — Gmail post extras. Sender/group
     titles ("Meta Account Security") + snippet text often embed the code
     ("Use 16919622 to get back into ...").
  2. Gmail inbox UI — ``monkey -p com.google.android.gm`` then
     ``uiautomator dump``: each row is a text node carrying
     "Use <code> to get back into your..." — the code is in the dump.

Usage:
  phone_gmail_otp.py                      # poll 120s, sender-scoped
  phone_gmail_otp.py --sender instagram   # narrow to one sender
  phone_gmail_otp.py --once --open        # single Gmail-app scan
  phone_gmail_otp.py --timeout 300

Env: PHONE_ADB (adb binary), PHONE_ADB_SERIAL (e.g. 100.84.31.85:5555),
     PHONE_OTP_SENDERS (extra |-separated sender keywords).

Prints the code to stdout; exit 0 found / 1 timeout.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time

ADB = os.environ.get("PHONE_ADB", "adb")
SERIAL = os.environ.get("PHONE_ADB_SERIAL", "")
DEFAULT_SENDERS = r"meta|facebook|whatsapp|instagram|linkedin"
SENDERS = (os.environ.get("PHONE_OTP_SENDERS") or DEFAULT_SENDERS)
CODE_RE = re.compile(r"\b(\d{6,8})\b")
NOTIF_RE = re.compile(
    r"android\.(?:title|text|bigText)=String \(([^)]*)\)")


def sh(*args: str, timeout: int = 15) -> str:
    cmd = [ADB] + ([f"-s", SERIAL] if SERIAL else []) + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ""
    except Exception as e:
        print(f"adb {' '.join(args[:2])} failed: {e}", file=sys.stderr)
        return ""


def notif_codes(sender_re: re.Pattern) -> list[str]:
    """Codes from Gmail notification extras, newest record blocks first."""
    out = sh("shell", "dumpsys", "notification", "--noredact", timeout=20)
    hits: list[str] = []
    for block in out.split("NotificationRecord"):
        if "com.google.android.gm" not in block:
            continue
        texts = NOTIF_RE.findall(block)
        joined = " ".join(texts)
        if not sender_re.search(joined):
            continue
        m = CODE_RE.search(joined)
        if m:
            hits.append(m.group(1))
    return hits


def inbox_codes(sender_re: re.Pattern) -> list[str]:
    """Codes visible in the Gmail inbox list UI."""
    sh("shell", "monkey", "-p", "com.google.android.gm",
       "-c", "android.intent.category.LAUNCHER", "1", timeout=15)
    time.sleep(4)
    sh("shell", "uiautomator", "dump", "/sdcard/_otp.xml", timeout=20)
    xml = sh("shell", "cat", "/sdcard/_otp.xml", timeout=15)
    hits: list[str] = []
    for t in re.findall(r'text="([^"]+)"', xml):
        if sender_re.search(t) or CODE_RE.search(t):
            m = CODE_RE.search(t)
            if m:
                hits.append(m.group(1))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sender", default="",
                    help="extra sender keyword merged into the sender regex")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--open", action="store_true",
                    help="skip the notification lane, scan the Gmail app")
    args = ap.parse_args()

    sender_re = re.compile(
        f"(?:{SENDERS}|{args.sender})" if args.sender else f"(?:{SENDERS})",
        re.I)

    deadline = time.time() + args.timeout
    while True:
        codes = inbox_codes(sender_re) if args.open else notif_codes(sender_re)
        if codes:
            print(codes[0])
            return 0
        if not args.open:
            # notification lane empty → try the inbox once per poll anyway
            codes = inbox_codes(sender_re)
            if codes:
                print(codes[0])
                return 0
        if args.once or time.time() > deadline:
            print("no OTP found", file=sys.stderr)
            return 1
        time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
