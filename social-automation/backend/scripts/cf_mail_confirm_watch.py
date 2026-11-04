#!/usr/bin/env python3
"""cf_mail_confirm_watch.py — auto-click the Cloudflare destination-confirmation mail.

Watches the mailbox that receives *@cloudless.gr for the CF "confirm your
address" mail and follows the verify link — closing the send_email
destination gate without the user touching webmail.

Source order (first that works):
  1. OMV_IMAP_* env -> direct dovecot IMAP (omv-ha, port 993)
  2. mail-ingest / Gmail watch lanes already on this box (checks env keys
     GMAIL_IMAP_* and MAIL_INGEST_* for a readable mailbox)

Env: OMV_IMAP_HOST / OMV_IMAP_USER / OMV_IMAP_PASS (or the Gmail lane equivalents).
Usage: python backend/scripts/cf_mail_confirm_watch.py [--for tbaltzakis@cloudless.gr] [--tries 40]
"""

from __future__ import annotations

import argparse
import imaplib
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

env_file = Path(__file__).resolve().parents[3] / ".env"


def _env(key: str, default: str = "") -> str:
    if val := os.environ.get(key):
        return val.strip()
    try:
        for line in env_file.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


def _mailboxes(target: str) -> list[tuple[str, str, str, str]]:
    boxes = []
    if h := _env("OMV_IMAP_HOST"):
        boxes.append((h, _env("OMV_IMAP_USER", target), _env("OMV_IMAP_PASS"), "omv-dovecot"))
    if _env("GMAIL_IMAP_USER") and _env("GMAIL_IMAP_PASS"):
        boxes.append(("imap.gmail.com", _env("GMAIL_IMAP_USER"), _env("GMAIL_IMAP_PASS"), "gmail"))
    return boxes


def _scan(box: imaplib.IMAP4_SSL, target_local: str) -> str | None:
    box.select("INBOX")
    for crit in ('(FROM "no-reply@notify.cloudflare.com")', '(FROM "cloudflare")', '(SUBJECT "verify")'):
        typ, ids = box.search(None, crit)
        if typ != "OK":
            continue
        for mid in reversed(ids[0].split()[-10:]):
            typ, data = box.fetch(mid, "(RFC822)")
            body = data[0][1].decode("utf-8", "replace") if typ == "OK" else ""
            m = re.search(r"https://[^\"'\s<>]+confirm[^\"'\s<>]+", body) or re.search(
                r"https://[^\"'\s<>]+verify[^\"'\s<>]+", body)
            if m and (target_local in body or target_local == ""):
                return m.group(0)
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--for", dest="target", default="tbaltzakis@cloudless.gr")
    p.add_argument("--tries", type=int, default=40)
    p.add_argument("--wait", type=int, default=15)
    args = p.parse_args()
    boxes = _mailboxes(args.target)
    if not boxes:
        print("[FIX] no readable mailbox configured")
        print("      -> set OMV_IMAP_HOST/USER/PASS (dovecot on omv-ha) or")
        print("         open webmail.cloudless.gr and click the CF confirm link manually")
        return 1

    local = args.target.split("@")[0]
    deadline = time.time() + args.tries * args.wait
    print(f"[ok ] watching {', '.join(b[3] for b in boxes)} for the CF confirm mail ({args.target})")
    while time.time() < deadline:
        for host, user, pw, name in boxes:
            if not pw:
                continue
            try:
                box = imaplib.IMAP4_SSL(host, 993)
                box.login(user, pw)
                link = _scan(box, local)
                box.logout()
            except (imaplib.IMAP4.error, OSError):
                continue
            if link:
                try:
                    urllib.request.urlopen(urllib.request.Request(link), timeout=20)
                    print(f"[ok ] followed confirm link from {name} — destination verified")
                    return 0
                except Exception as exc:
                    print(f"[FIX] link found but follow failed: {exc}")
                    print(f"      -> open manually: {link}")
                    return 1
        time.sleep(args.wait)
    print("[FIX] no confirm mail seen in the watch window — check the mailbox manually")
    return 1


if __name__ == "__main__":
    sys.exit(main())
