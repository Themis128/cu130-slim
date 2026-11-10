#!/usr/bin/env python3
"""Watch the Galaxy S22+ for a WhatsApp/Meta phone-verification code.

Polls (every POLL_SECONDS, up to TIMEOUT_H hours):
  1. SMS inbox via content://sms/inbox (Meta/Facebook/WhatsApp senders)
  2. Notification shade via dumpsys notification (WhatsApp Business app
     delivers verification codes as in-app messages -> notifications,
     readable even while the cover is closed)

On a 6-digit code match, writes it to ~/logs/wa-code.txt (mode 600) and
exits 0. Fails safe: ADB errors are retried, never fatal.

Env overrides: ADB_BIN, ADB_SERIAL, POLL_SECONDS, TIMEOUT_H.
"""
import os
import re
import subprocess
import sys
import time

ADB_BIN = os.environ.get(
    "ADB_BIN",
    "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe",
)
SERIAL = os.environ.get("ADB_SERIAL", "RFCTA0AEKZP")
POLL = int(os.environ.get("POLL_SECONDS", "30"))
TIMEOUT_H = float(os.environ.get("TIMEOUT_H", "24"))
OUT = os.path.expanduser("~/logs/wa-code.txt")
LOG = os.path.expanduser("~/logs/wa-code-watch.log")

CODE_RE = re.compile(r"\b(\d{6})\b")
SENDER_RE = re.compile(r"meta|facebook|whatsapp|whats app", re.I)
BODY_CODE_RE = re.compile(r"(?:code|verification|verify|pin)[^\d]{0,40}(\d{6})|(\d{6})[^\d]{0,40}(?:code|verification|verify)", re.I)


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def adb(args: list[str], timeout: int = 30) -> str:
    try:
        r = subprocess.run(
            [ADB_BIN, "-s", SERIAL, "shell", *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.stdout or ""
    except Exception as e:
        return f"__ERR__{e}"


def check_sms() -> str | None:
    out = adb([
        "content", "query", "--uri", "content://sms/inbox",
        "--projection", "address,body,date", "--sort", "date DESC",
    ])
    if "__ERR__" in out[:20]:
        return None
    # only the newest ~15 rows matter
    for m in re.finditer(r"address=([^,\n]+),\s*body=([^\n]*?)(?:,\s*date=|$)", out):
        sender, body = m.group(1), m.group(2)
        if not SENDER_RE.search(sender) and not SENDER_RE.search(body):
            continue
        hit = BODY_CODE_RE.search(body) or CODE_RE.search(body)
        if hit:
            return hit.group(1) or hit.group(2)
    return None


def check_notifications() -> str | None:
    out = adb(["dumpsys", "notification", "--noredact"], timeout=60)
    if "__ERR__" in out[:20] or not out:
        return None
    # slice into per-notification chunks mentioning whatsapp/meta
    for chunk in re.split(r"NotificationRecord\(", out):
        if not re.search(r"whatsapp|facebook|meta\b", chunk, re.I):
            continue
        for field in re.findall(r"android\.(?:text|bigText|title)=String \((.*?)\)", chunk):
            hit = BODY_CODE_RE.search(field) or CODE_RE.search(field)
            if hit:
                return hit.group(1) or hit.group(2) or hit.group(0)
    return None


def main() -> int:
    deadline = time.time() + TIMEOUT_H * 3600
    log(f"wa-code-watch start serial={SERIAL} poll={POLL}s timeout={TIMEOUT_H}h")
    while time.time() < deadline:
        for name, fn in (("sms", check_sms), ("notif", check_notifications)):
            try:
                code = fn()
            except Exception as e:
                log(f"{name} check error: {e}")
                continue
            if code:
                log(f"CODE FOUND via {name}: {code}")
                fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(code + "\n")
                return 0
        time.sleep(POLL)
    log("timeout reached, no code")
    return 1


if __name__ == "__main__":
    sys.exit(main())
