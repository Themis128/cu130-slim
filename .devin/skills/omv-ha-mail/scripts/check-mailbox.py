#!/usr/bin/env python3
"""Verify mailbox credentials by attempting SASL login to omv-ha postfix.
Requires MAILBOX_PASSWORD environment variable."""

import smtplib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env  # noqa: E402

SMTP_HOST = env("SMTP_HOST") or "192.168.1.130"
SMTP_PORT = int(env("SMTP_PORT") or 587)
MAIL_FROM = env("MAIL_FROM") or "tbaltzakis@cloudless.gr"
password = env("MAILBOX_PASSWORD")

if not password:
    print("ERROR: MAILBOX_PASSWORD environment variable is not set", file=sys.stderr)
    sys.exit(1)

print("=== Mailbox Auth Check ===")
print(f"Host: {SMTP_HOST}:{SMTP_PORT}")
print(f"User: {MAIL_FROM}\n")

try:
    server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(MAIL_FROM, password)
    print("  AUTH: OK — mailbox credentials valid")
    server.quit()
    print("\n  Mailbox auth: OK")
except smtplib.SMTPAuthenticationError as e:
    print(f"  AUTH FAILED: {e}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"  ERROR: {e}", file=sys.stderr)
    sys.exit(1)
