#!/usr/bin/env bash
# Read inbox via IMAP from omv-ha dovecot.
# Lists recent messages (subject, from, date) without downloading full body.
#
# Environment:
#   MAILBOX_PASSWORD  — required
#   IMAP_HOST         — optional (default: 192.168.1.130)
#   IMAP_PORT         — optional (default: 993)
#   MAIL_USER         — optional (default: tbaltzakis@cloudless.gr)
#   MAX_MESSAGES      — optional (default: 10)
#
set -euo pipefail

IMAP_HOST="${IMAP_HOST:-192.168.1.130}"
IMAP_PORT="${IMAP_PORT:-993}"
MAIL_USER="${MAIL_USER:-tbaltzakis@cloudless.gr}"
MAX_MESSAGES="${MAX_MESSAGES:-10}"

if [ -z "${MAILBOX_PASSWORD:-}" ]; then
  echo "ERROR: MAILBOX_PASSWORD environment variable is not set" >&2
  echo "Set it with: export MAILBOX_PASSWORD='your-password'" >&2
  exit 1
fi

echo "=== Inbox: ${MAIL_USER} @ ${IMAP_HOST}:${IMAP_PORT} ==="
echo ""

python3 - "$IMAP_HOST" "$IMAP_PORT" "$MAIL_USER" "$MAILBOX_PASSWORD" "$MAX_MESSAGES" << 'PYEOF'
import sys
import imaplib
import email
from email.header import decode_header

host = sys.argv[1]
port = int(sys.argv[2])
user = sys.argv[3]
password = sys.argv[4]
max_msgs = int(sys.argv[5])

def decode_str(s):
    if s is None:
        return ""
    parts = decode_header(s)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)

try:
    mail = imaplib.IMAP4_SSL(host, port)
    mail.login(user, password)
    mail.select("INBOX")

    # Search all messages
    status, messages = mail.search(None, "ALL")
    msg_ids = messages[0].split()
    total = len(msg_ids)

    print(f"  Total messages: {total}")
    print(f"  Showing last {min(max_msgs, total)}:")
    print()

    # Get last N messages
    recent_ids = msg_ids[-max_msgs:] if total > max_msgs else msg_ids

    for msg_id in reversed(recent_ids):
        status, msg_data = mail.fetch(msg_id, "(RFC822.HEADER)")
        if status != "OK":
            continue
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        subject = decode_str(msg.get("Subject", ""))
        from_ = decode_str(msg.get("From", ""))
        date_ = msg.get("Date", "")

        print(f"  [{msg_id.decode()}] {subject[:60]}")
        print(f"    From: {from_[:50]}")
        print(f"    Date: {date_}")
        print()

    mail.logout()
    print("  Inbox read: OK")
except Exception as e:
    print(f"  ERROR: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
