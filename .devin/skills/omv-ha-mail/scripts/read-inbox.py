#!/usr/bin/env python3
"""Read inbox via IMAP from omv-ha dovecot.
Lists recent messages (subject, from, date) without downloading full body.

Environment: MAILBOX_PASSWORD (required), IMAP_HOST, IMAP_PORT,
MAIL_USER, MAX_MESSAGES."""

import email
import imaplib
import sys
from email.header import decode_header
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env  # noqa: E402

IMAP_HOST = env("IMAP_HOST") or "192.168.1.130"
IMAP_PORT = int(env("IMAP_PORT") or 993)
MAIL_USER = env("MAIL_USER") or "tbaltzakis@cloudless.gr"
MAX_MESSAGES = int(env("MAX_MESSAGES") or 10)
password = env("MAILBOX_PASSWORD")

if not password:
    print("ERROR: MAILBOX_PASSWORD environment variable is not set", file=sys.stderr)
    sys.exit(1)


def decode_str(s):
    if s is None:
        return ""
    return "".join(
        p.decode(c or "utf-8", errors="replace") if isinstance(p, bytes) else p
        for p, c in decode_header(s))


print(f"=== Inbox: {MAIL_USER} @ {IMAP_HOST}:{IMAP_PORT} ===\n")
try:
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(MAIL_USER, password)
    mail.select("INBOX")

    _, messages = mail.search(None, "ALL")
    msg_ids = messages[0].split()
    total = len(msg_ids)
    print(f"  Total messages: {total}")
    print(f"  Showing last {min(MAX_MESSAGES, total)}:\n")

    for msg_id in reversed(msg_ids[-MAX_MESSAGES:]):
        status, msg_data = mail.fetch(msg_id, "(RFC822.HEADER)")
        if status != "OK":
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        print(f"  [{msg_id.decode()}] {decode_str(msg.get('Subject', ''))[:60]}")
        print(f"    From: {decode_str(msg.get('From', ''))[:50]}")
        print(f"    Date: {msg.get('Date', '')}\n")

    mail.logout()
    print("  Inbox read: OK")
except Exception as e:
    print(f"  ERROR: {e}", file=sys.stderr)
    sys.exit(1)
