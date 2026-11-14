#!/usr/bin/env python3
"""x_phone_bridge — fetch X SMS verification codes for the Windows x_daemon.

One-shot per cron tick (every 2 min):
  1. Windows x_daemon writes x_phone_needed.json {ts,url} when it lands on
     an X phone/SMS checkpoint.
  2. This script reads the newest X/Twitter SMS via the phone lanes proven
     in human-gate-bridges/wa_verify.py — adb content provider first,
     then Termux SSH termux-sms-list.
  3. Writes x_phone_code.json {code, ts} back to C:\\Users\\tbaltzakis\\ —
     the daemon polls it and enters the code.

State: ~/.local/state/x-phone-bridge — remembers which need_ts was answered
and gives up on challenges older than 10 min.

Deployed copy: ~/scripts/x_phone_bridge.py (cron runs that path) — kept
in sync with this canonical; the 2026-11-14 popup sweep dropped the
adb.exe fallbacks here too (every adb.exe spawn flashes a console
window on the */2min cadence when a challenge is active).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

DIR = "/mnt/c/Users/tbaltzakis"
NEED = os.path.join(DIR, "x_phone_needed.json")
OUT = os.path.join(DIR, "x_phone_code.json")
STATE = os.path.expanduser("~/.local/state/x-phone-bridge/state.json")
LOG = os.path.expanduser("~/logs/x-phone-bridge.log")
KEY = os.path.expanduser("~/.ssh/id_rsa_win")
PHONE_IP = os.environ.get("PHONE_ADB_IP", "192.168.1.77")
NEED_TTL = 600  # give up on a challenge flag older than 10 min

ADBS = ["adb"]  # Linux only — every adb.exe spawn flashes a console window
X_SENDER = re.compile(r"twitter|x\.com|\bX\b|verify", re.I)
CODE_RE = re.compile(r"\b(\d{5,8})\b")


def log(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def _code_from_lines(lines: list[str]) -> str | None:
    # X-only: never fall back to unrelated SMS — a Google/other-service code
    # entered into X's checkpoint would burn the attempt
    for l in lines:
        if X_SENDER.search(l):
            m = CODE_RE.search(l)
            if m:
                return m.group(1)
    return None


def adb_code() -> str | None:
    for adb in ADBS:
        try:
            r = subprocess.run(
                [adb, "shell", "content", "query", "--uri",
                 "content://sms/inbox", "--projection", "address,date,body",
                 "--sort", "date DESC"],
                capture_output=True, text=True, timeout=8)
            code = _code_from_lines(r.stdout.splitlines()[-40:])
            if code:
                return code
        except Exception:
            continue
    return None


def termux_code() -> str | None:
    try:
        r = subprocess.run(
            ["ssh", "-p", "8022", "-o", "ConnectTimeout=8",
             "-o", "StrictHostKeyChecking=no", "-i", KEY,
             f"u0_a357@{PHONE_IP}", "termux-sms-list -l 8"],
            capture_output=True, text=True, timeout=15)
        return _code_from_lines(r.stdout.splitlines())
    except Exception:
        return None


def main() -> int:
    if not os.path.exists(NEED):
        return 0
    try:
        need = json.load(open(NEED))
        need_ts = float(need.get("ts") or 0)
    except Exception:
        return 0
    if time.time() - need_ts > NEED_TTL:
        return 0
    try:
        st = json.load(open(STATE))
        if st.get("need_ts") == need_ts:
            return 0  # already answered this challenge
    except Exception:
        pass

    code = adb_code() or termux_code()
    if not code:
        return 0  # SMS may not have landed yet — retry next tick

    json.dump({"code": code, "ts": time.time()}, open(OUT, "w"))
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump({"need_ts": need_ts}, open(STATE, "w"))
    log(f"code delivered for need_ts={need_ts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
