#!/usr/bin/env python3
"""wa_verify.py — WhatsApp phone verify via Graph API.
Polls ADB SMS + the mailbox (SMS→email forward) for the code, then
verify_code. Last resort: wa_verify.py <code>.

    wa_verify.py            # request code → poll ADB + mail → verify
    wa_verify.py <code>     # manual fallback
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

CID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
TOK = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
KEY = os.path.expanduser("~/.ssh/id_rsa_win")
USER = os.environ.get("OMV_SSH_USER", "")
HOST = os.environ.get("OMV_SSH_HOST", "")
CODE_RE = re.compile(r"\b([0-9]{6})\b")


def graph(path: str, **params) -> dict:
    r = urllib.request.Request(
        f"https://graph.facebook.com/v21.0/{path.lstrip('/')}",
        data="&".join(f"{k}={v}" for k, v in params.items()).encode(),
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(r, timeout=15) as f:
            return json.loads(f.read())
    except Exception as e:
        return {"error": str(e)}


ADBS = ["adb",
        "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe",
        "/mnt/c/platform-tools/adb.exe"]


def adb_code() -> str | None:
    for adb in ADBS:
        try:
            r = subprocess.run(
                [adb, "shell", "content", "query", "--uri", "content://sms/inbox",
                 "--projection", "address,date,body", "--sort", "date DESC"],
                capture_output=True, text=True, timeout=8)
            # Meta/WhatsApp senders only — a generic 6-digit match can grab
            # other services' codes (e.g. the 'Link' 246930 false positive)
            rows = [l for l in r.stdout.splitlines() if re.search(  # noqa: E741
                r'whatsapp|facebook|meta|AUTHMSG', l, re.I)]
            m = CODE_RE.search(" ".join(rows)) if rows else CODE_RE.search(r.stdout)
            if m:
                return m.group(1)
        except Exception:
            continue
    return None


PHONE_IP = os.environ.get("PHONE_ADB_IP", "192.168.1.77")


def termux_code() -> str | None:
    """Termux SSH lane — termux-sms-list over :8022 (needs READ_SMS grant once)."""
    try:
        r = subprocess.run(
            ["ssh", "-p", "8022", "-o", "ConnectTimeout=8",
             "-o", "StrictHostKeyChecking=no", "-i", KEY,
             f"u0_a357@{PHONE_IP}", "termux-sms-list -l 5"],
            capture_output=True, text=True, timeout=12)
        rows = [l for l in r.stdout.splitlines() if re.search(
            r'whatsapp|facebook|meta|AUTHMSG', l, re.I)]
        m = CODE_RE.search(" ".join(rows)) if rows else CODE_RE.search(r.stdout)
        return m.group(1) if m else None
    except Exception:
        return None


def mail_code() -> str | None:
    """SMS→email forward lands in the inbox; doveadm scans it for a 6-digit code."""
    if not (USER and HOST):
        return None
    r = subprocess.run(
        ["ssh", "-i", KEY, f"{USER}@{HOST}",
         "sudo -n doveadm fetch -u tbaltzakis@cloudless.gr 'uid body' "
         "mailbox INBOX SINCE 1h 2>/dev/null | tail -120"],
        capture_output=True, text=True, timeout=20)
    m = CODE_RE.search(r.stdout)
    return m.group(1) if m else None


def main() -> int:
    code = sys.argv[1] if len(sys.argv) > 1 else None
    if not (CID and TOK):
        sys.exit("need WHATSAPP_PHONE_NUMBER_ID + WHATSAPP_ACCESS_TOKEN")
    if not code:
        out = graph(f"{CID}/request_code", code_method=os.environ.get("WA_CODE_METHOD", "SMS"), language="en_US")
        print("request_code:", out)
        for _ in range(24):
            code = adb_code() or termux_code() or mail_code()
            if code:
                break
            time.sleep(10)
    if not code:
        sys.exit("no code found — run: wa_verify.py <code>")
    print("verify_code:", graph(f"{CID}/verify_code", code=code))
    return 0


if __name__ == "__main__":
    sys.exit(main())
