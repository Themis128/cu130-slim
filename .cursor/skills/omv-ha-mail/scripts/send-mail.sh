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
#   IMAP_HOST         — optional (default: same as SMTP_HOST)
#   IMAP_PORT         — optional (default: 993)
#   MAIL_FROM         — optional (default: tbaltzakis@cloudless.gr)
#   SAVE_SENT         — optional (default: true, set to "false" to skip)
#
set -euo pipefail

SMTP_HOST="${SMTP_HOST:-192.168.1.130}"
SMTP_PORT="${SMTP_PORT:-587}"
IMAP_HOST="${IMAP_HOST:-$SMTP_HOST}"
IMAP_PORT="${IMAP_PORT:-993}"
MAIL_FROM="${MAIL_FROM:-tbaltzakis@cloudless.gr}"
SAVE_SENT="${SAVE_SENT:-true}"

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

# Build and send via Python (handles MIME, attachments, STARTTLS, SASL, IMAP Sent copy)
python3 - "$TO" "$SUBJECT" "$BODY" "$ATTACH" "$MAIL_FROM" "$SMTP_HOST" "$SMTP_PORT" "$MAILBOX_PASSWORD" "$IMAP_HOST" "$IMAP_PORT" "$SAVE_SENT" << 'PYEOF'
import sys
import smtplib
import imaplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formatdate
import os

to = sys.argv[1]
subject = sys.argv[2]
body = sys.argv[3]
attach_path = sys.argv[4]
mail_from = sys.argv[5]
smtp_host = sys.argv[6]
smtp_port = int(sys.argv[7])
password = sys.argv[8]
imap_host = sys.argv[9]
imap_port = int(sys.argv[10])
save_sent = sys.argv[11].lower() != "false"

msg = MIMEMultipart()
msg['From'] = mail_from
msg['To'] = to
msg['Subject'] = subject
msg['Date'] = formatdate(localtime=True)
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

    # Save copy to Sent folder via IMAP
    if save_sent:
        try:
            imap = imaplib.IMAP4_SSL(imap_host, imap_port)
            imap.login(mail_from, password)
            # Try common Sent folder names
            sent_folder = None
            for folder in ["Sent", "INBOX.Sent", "Sent Items", "INBOX/Sent"]:
                typ, data = imap.list(folder)
                if typ == "OK" and data and data[0]:
                    sent_folder = folder
                    break
            if not sent_folder:
                # Create Sent folder if it doesn't exist
                imap.create("Sent")
                sent_folder = "Sent"
            imap.append(sent_folder, "\\Seen", imaplib.Time2Internaldate(imaplib.Time2Internaldate(0)), msg.as_bytes())
            imap.logout()
            print(f"  Saved copy to: {sent_folder} (via IMAP)")
        except Exception as imap_err:
            print(f"  WARNING: Could not save Sent copy: {imap_err}", file=sys.stderr)

except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
