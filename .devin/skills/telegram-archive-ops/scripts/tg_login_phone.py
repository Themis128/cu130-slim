#!/usr/bin/env python3
"""tg_login_phone.py — one-time Telethon login for telegram_archive.py,
with the login code read off the phone over wireless ADB (tailnet).

Flow:
  1. adb connect <tailnet-ip>:5555  (Galaxy S22+, wireless debugging)
  2. spawn `telegram_archive.py login` — Telegram sends the code to the
     Telegram app's "Telegram" service chat + a system notification
  3. poll `adb shell dumpsys notification` for the 5-digit code
  4. feed it to the login prompt; if 2FA is enabled, TG_2FA_PASSWORD
     env (or --password) supplies it — never logged

Prereqs: TG_API_ID + TG_API_HASH in env or repo .env (my.telegram.org app),
phone number via --phone or TG_PHONE, phone on tailnet with wireless ADB.

    tg_login_phone.py --phone +30XXXXXXXXX
    tg_login_phone.py --phone +30XXXXXXXXX --password-stdin   # read 2FA pw
"""
from __future__ import annotations

import argparse
import os
import re
import select
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
VENV_PY = Path.home() / ".local/share/telegram-archive/venv/bin/python"
ARCHIVE = REPO / "scripts/telegram_archive.py"
# Prefer USB-C (user directive). WSL2 cannot see USB devices — route USB
# serials through the Windows adb.exe (platform-tools on C:); network
# serials can use either server. TG_ADB_EXE overrides the Windows path.
USB_SERIAL = os.environ.get("TG_ADB_USB", "")
DEFAULT_ADB = os.environ.get("TG_ADB_TARGET", "100.84.31.85:5555")
WIN_ADB_CANDIDATES = [
    os.environ.get("TG_ADB_EXE", ""),
    "/mnt/c/Users/tbaltzakis/Android/Sdk/platform-tools/adb.exe",
    "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe",
]


def adb_bin():
    for c in WIN_ADB_CANDIDATES:
        if c and Path(c).exists():
            return c
    return "adb"


def sh(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def adb(serial, *args, timeout=15):
    return sh([adb_bin(), "-s", serial, "shell", *args], timeout=timeout)


def _device_serials():
    out = sh([adb_bin(), "devices"]).stdout
    return [l.split("\t")[0] for l in out.splitlines()[1:]
            if "\tdevice" in l]


def ensure_adb(target):
    """USB first (TG_ADB_USB or any usb serial), tailnet ADB as fallback."""
    serials = _device_serials()
    usb = USB_SERIAL or next((s for s in serials if ":" not in s), None)
    if usb:
        return usb
    if target not in serials:
        sh([adb_bin(), "connect", target], timeout=20)
        serials = _device_serials()
    live = target if target in serials else (serials[0] if serials else None)
    if not live:
        sys.exit("adb: no device — plug in USB-C or enable wireless debugging")
    return live


def _extract_code(text: str) -> str | None:
    """Pull the 5-digit login code out of a notification android.text line."""
    # android.text=String (Login code: 12345. Do not give...) — Samsung
    # redacts to 'String [length=N]' without --noredact; we always pass it.
    for m in re.finditer(r"android\.text=(?:String \()?([^)]*)", text):
        body = m.group(1)
        if not re.search(r"login code|sign.?in code|κωδικός|code", body, re.I):
            continue
        digits = re.search(r"(?<!\d)(\d{5})(?!\d)", body)
        if digits:
            return digits.group(1)
    # looser fallback: any 5-digit run inside a telegram-ish text blob
    for m in re.finditer(r"android\.text=String \(([^)]{0,300})", text):
        body = m.group(1)
        if re.search(r"telegram|login|code", body, re.I):
            digits = re.search(r"(?<!\d)(\d{5})(?!\d)", body)
            if digits:
                return digits.group(1)
    return None


def fetch_login_code(serial, timeout_s=180):
    """Poll `adb shell dumpsys notification --noredact` for the Telegram
    login-code notification (arrives in the shade as 'Login code: NNNNN')."""
    deadline = time.time() + timeout_s
    seen = set()
    while time.time() < deadline:
        out = adb(serial, "dumpsys", "notification", "--noredact", timeout=20)
        text = out.stdout + out.stderr
        # scope to Telegram's package block when possible
        tg_blocks = re.findall(
            r"pkg=org\.telegram\.messenger.*?(?=NotificationRecord|\Z)",
            text, re.S)
        for blob in tg_blocks or [text]:
            code = _extract_code(blob)
            if code and code not in seen:
                seen.add(code)
                # brief settle so a fresher code (re-send) can supersede
                time.sleep(2)
                return code
        time.sleep(3)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phone", default=os.environ.get("TG_PHONE", ""))
    ap.add_argument("--adb", default=DEFAULT_ADB)
    ap.add_argument("--password", default=os.environ.get("TG_2FA_PASSWORD", ""))
    ap.add_argument("--password-stdin", action="store_true", help="read 2FA password from stdin")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()
    if not args.phone:
        sys.exit("--phone or TG_PHONE required (E.164, e.g. +30XXXXXXXXX)")

    serial = ensure_adb(args.adb)
    print(f"[adb] {serial} connected ({'usb' if ':' not in serial else 'network'})")

    py = str(VENV_PY) if VENV_PY.exists() else sys.executable
    proc = subprocess.Popen(
        [py, str(ARCHIVE), "login", "--phone", args.phone],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, cwd=str(REPO), bufsize=1,
    )

    # Stream output until the code prompt appears
    code_sent = False
    password = args.password or (sys.stdin.readline().strip() if args.password_stdin else "")
    buf = ""
    deadline = time.time() + args.timeout
    try:
        fd = proc.stdout.fileno()
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 1.0)
            if not r:
                if proc.poll() is not None:
                    break
                continue
            ch = os.read(fd, 4096).decode(errors="replace")
            if not ch:
                break
            buf += ch
            sys.stdout.write(ch)
            sys.stdout.flush()
            if not code_sent and "code from Telegram" in buf:
                print("\n[phone] waiting for Telegram login code notification…")
                code = fetch_login_code(serial, timeout_s=args.timeout)
                if not code:
                    sys.exit("\n[phone] no code seen in notifications — check the phone manually")
                print(f"[phone] code captured: {code}")
                proc.stdin.write(code + "\n")
                proc.stdin.flush()
                code_sent = True
                buf = ""
            if code_sent and "2FA password" in buf:
                if not password:
                    sys.exit("\n2FA enabled — rerun with --password or TG_2FA_PASSWORD set")
                proc.stdin.write(password + "\n")
                proc.stdin.flush()
                password = ""  # write once
                buf = ""
            if "authorized as" in buf:
                break
        proc.wait(timeout=30)
    except KeyboardInterrupt:
        proc.kill()
        raise
    print(f"\n[done] exit={proc.returncode}")
    sys.exit(proc.returncode or 0)


if __name__ == "__main__":
    main()
