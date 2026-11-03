#!/usr/bin/env python3
"""vetting_watch.py — poll the mailbox for LinkedIn's Microsoft Vetting
Services / CM-API review email, fire a Telegram alert when it lands.
    vetting_watch.py         # poll loop, 10-min interval
    vetting_watch.py --once  # single check
"""
import subprocess
import sys
import time
import urllib.request
import json
import os


def _inbox_hit() -> str:
    # doveadm fetch via ssh omv-ha — same path as mail-code-fetch, searching
    # for linkedin/vetting/microsoft in subjects over the last 7 days
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "omv-ha",
             "sudo -n doveadm fetch -u tbaltzakis@cloudless.gr 'subject' "
             "mailbox '*' SUBJECT linkedin SENTSINCE 7d 2>/dev/null | head -20"],
            capture_output=True, text=True, timeout=30)
        return r.stdout.strip()
    except Exception:
        return ""


def _tg(text: str):
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = "7017840434"
    if not tok:
        return
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    urllib.request.urlopen(urllib.request.Request(
        url, data=json.dumps({"chat_id": chat, "text": text}).encode(),
        headers={"Content-Type": "application/json"}), timeout=10)


def main() -> int:
    once = "--once" in sys.argv
    while True:
        hit = _inbox_hit()
        if hit:
            _tg(f"LinkedIn CM-API review — vetting/review email landed:\n{hit[:200]}")
            print("alerted:", hit[:100])
            return 0
        if once:
            print("no vetting email yet")
            return 1
        time.sleep(600)


if __name__ == "__main__":
    sys.exit(main())
