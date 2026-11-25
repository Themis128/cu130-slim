#!/usr/bin/env python3
"""Send email through omv-ha postfix (relay via Resend).

Usage:
  send-mail.py --to "recipient@example.com" --subject "Subject" --body "Body text"
  send-mail.py --to "recipient@example.com" --subject "Subject" --body "Body" --attach "/path/file"
  send-mail.py --to "recipient@example.com" --subject "Subject" --body-file /path/to/body.txt

Environment: MAILBOX_PASSWORD (required), SMTP_HOST, SMTP_PORT, IMAP_HOST,
IMAP_PORT, MAIL_FROM, SAVE_SENT."""

import imaplib
import mimetypes
import os
import smtplib
import sys
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env, usage  # noqa: E402

# The postfix relay listens on the LAN (192.168.1.130:587). .env sets
# SMTP_HOST=mail.cloudless.gr which hangs on connect from this host — try
# the LAN IP first, then the configured host as fallback.
SMTP_HOST_CFG = env("SMTP_HOST") or "192.168.1.130"
SMTP_HOSTS = list(dict.fromkeys(["192.168.1.130", SMTP_HOST_CFG]))
SMTP_PORT = int(env("SMTP_PORT") or 587)
IMAP_HOST = env("IMAP_HOST") or SMTP_HOST_CFG
IMAP_PORT = int(env("IMAP_PORT") or 993)
MAIL_FROM = env("MAIL_FROM") or "tbaltzakis@cloudless.gr"
SAVE_SENT = (env("SAVE_SENT") or "true").lower() != "false"

to = subject = body = body_file = attach = ""
i = 1
while i < len(sys.argv):
    k, v = sys.argv[i], (sys.argv[i + 1] if i + 1 < len(sys.argv) else "")
    if k == "--to":
        to = v
    elif k == "--subject":
        subject = v
    elif k == "--body":
        body = v
    elif k == "--body-file":
        body_file = v
    elif k == "--attach":
        attach = v
    elif k in ("--help", "-h"):
        usage('send-mail.py --to ADDR --subject TEXT --body TEXT [--attach FILE] [--body-file FILE]')
    else:
        print(f"Unknown option: {k}", file=sys.stderr)
        sys.exit(1)
    i += 2

if not to or not subject:
    print("ERROR: --to and --subject are required", file=sys.stderr)
    sys.exit(1)
if not body and not body_file:
    print("ERROR: --body or --body-file is required", file=sys.stderr)
    sys.exit(1)
password = env("MAILBOX_PASSWORD")
if not password:
    print("ERROR: MAILBOX_PASSWORD environment variable is not set", file=sys.stderr)
    print("Set it with: export MAILBOX_PASSWORD='your-password'", file=sys.stderr)
    sys.exit(1)

if body_file:
    body = Path(body_file).read_text()

msg = MIMEMultipart()
msg["From"] = MAIL_FROM
msg["To"] = to
msg["Subject"] = subject
msg["Date"] = formatdate(localtime=True)
msg.attach(MIMEText(body, "plain"))

if attach and Path(attach).is_file():
    mime = mimetypes.guess_type(attach)[0] or "application/octet-stream"
    main_type, sub_type = mime.split("/", 1)
    part = MIMEBase(main_type, sub_type)
    part.set_payload(Path(attach).read_bytes())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment",
                    filename=os.path.basename(attach))
    msg.attach(part)
    print(f"  Attachment: {os.path.basename(attach)} ({os.path.getsize(attach)} bytes)")
elif attach:
    print(f"  WARNING: Attachment file not found: {attach}", file=sys.stderr)

last_err = None
for smtp_host in SMTP_HOSTS:
    try:
        server = smtplib.SMTP(smtp_host, SMTP_PORT, timeout=15)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(MAIL_FROM, password)
        server.sendmail(MAIL_FROM, [to], msg.as_string())
        server.quit()
        SMTP_HOST = smtp_host
        break
    except Exception as e:
        last_err = e
        print(f"  SMTP {smtp_host}:{SMTP_PORT} failed: {e}", file=sys.stderr)
else:
    print(f"ERROR: {last_err}", file=sys.stderr)
    sys.exit(1)
print(f"SUCCESS: Email sent to {to}")
print(f"  From: {MAIL_FROM}")
print(f"  Subject: {subject}")
print(f"  Via: {SMTP_HOST}:{SMTP_PORT} (STARTTLS + SASL)")

if SAVE_SENT:
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(MAIL_FROM, password)
        sent_folder = None
        for folder in ("Sent", "INBOX.Sent", "Sent Items", "INBOX/Sent"):
            typ, data = imap.list(folder)
            if typ == "OK" and data and data[0]:
                sent_folder = folder
                break
        if not sent_folder:
            imap.create("Sent")
            sent_folder = "Sent"
        imap.append(sent_folder, "\\Seen",
                    imaplib.Time2Internaldate(0), msg.as_bytes())
        imap.logout()
        print(f"  Saved copy to: {sent_folder} (via IMAP)")
    except Exception as e:
        print(f"  WARNING: Could not save Sent copy: {e}", file=sys.stderr)
