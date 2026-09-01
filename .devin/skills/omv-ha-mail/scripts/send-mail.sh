#!/usr/bin/env bash
# Send email through omv-ha postfix (relay via Resend).
#
# Usage:
#   send-mail.sh --to "recipient@example.com" --subject "Subject" --body "Body text"
#   send-mail.sh --to "recipient@example.com" --subject "Subject" --body "Body text" --attach "/path/to/file"
#   send-mail.sh --to "recipient@example.com" --subject "Subject" --body-file /path/to/body.txt
#
# Environment:
#   MAILBOX_PASSWORD  — required (tbaltzakis@cloudless.gr mailbox password)
#   SMTP_HOST         — optional (default: 192.168.1.130)
#   SMTP_PORT         — optional (default: 587)
#   MAIL_FROM         — optional (default: tbaltzakis@cloudless.gr)
#
set -euo pipefail

SMTP_HOST="${SMTP_HOST:-192.168.1.130}"
SMTP_PORT="${SMTP_PORT:-587}"
MAIL_FROM="${MAIL_FROM:-tbaltzakis@cloudless.gr}"

TO=""
SUBJECT=""
BODY=""
BODY_FILE=""
ATTACH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --to)        TO="$2"; shift 2 ;;
    --subject)   SUBJECT="$2"; shift 2 ;;
    --body)      BODY="$2"; shift 2 ;;
    --body-file) BODY_FILE="$2"; shift 2 ;;
    --attach)    ATTACH="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: $0 --to ADDR --subject TEXT --body TEXT [--attach FILE] [--body-file FILE]"
      echo "Env: MAILBOX_PASSWORD (required), SMTP_HOST, SMTP_PORT, MAIL_FROM"
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$TO" ] || [ -z "$SUBJECT" ]; then
  echo "ERROR: --to and --subject are required" >&2
  exit 1
fi

if [ -z "$BODY" ] && [ -z "$BODY_FILE" ]; then
  echo "ERROR: --body or --body-file is required" >&2
  exit 1
fi

if [ -z "${MAILBOX_PASSWORD:-}" ]; then
  echo "ERROR: MAILBOX_PASSWORD environment variable is not set" >&2
  echo "Set it with: export MAILBOX_PASSWORD='your-password'" >&2
  exit 1
fi

# Build body
if [ -n "$BODY_FILE" ]; then
  BODY=$(cat "$BODY_FILE")
fi

# Build and send via Python (handles MIME, attachments, STARTTLS, SASL)
python3 - "$TO" "$SUBJECT" "$BODY" "$ATTACH" "$MAIL_FROM" "$SMTP_HOST" "$SMTP_PORT" "$MAILBOX_PASSWORD" << 'PYEOF'
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import os

to = sys.argv[1]
subject = sys.argv[2]
body = sys.argv[3]
attach_path = sys.argv[4]
mail_from = sys.argv[5]
smtp_host = sys.argv[6]
smtp_port = int(sys.argv[7])
password = sys.argv[8]

msg = MIMEMultipart()
msg['From'] = mail_from
msg['To'] = to
msg['Subject'] = subject
msg.attach(MIMEText(body, 'plain'))

# Attach file if provided
if attach_path and os.path.isfile(attach_path):
    filename = os.path.basename(attach_path)
    # Determine MIME type
    import mimetypes
    mime_type, _ = mimetypes.guess_type(attach_path)
    if mime_type is None:
        mime_type = 'application/octet-stream'
    main_type, sub_type = mime_type.split('/', 1)

    with open(attach_path, 'rb') as f:
        part = MIMEBase(main_type, sub_type)
        part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment', filename=filename)
        msg.attach(part)
    print(f"  Attachment: {filename} ({os.path.getsize(attach_path)} bytes)")
elif attach_path:
    print(f"  WARNING: Attachment file not found: {attach_path}", file=sys.stderr)

# Send
try:
    server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(mail_from, password)
    server.sendmail(mail_from, [to], msg.as_string())
    server.quit()
    print(f"SUCCESS: Email sent to {to}")
    print(f"  From: {mail_from}")
    print(f"  Subject: {subject}")
    print(f"  Via: {smtp_host}:{smtp_port} (STARTTLS + SASL)")
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
