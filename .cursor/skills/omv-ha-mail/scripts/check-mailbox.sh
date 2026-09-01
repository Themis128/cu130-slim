#!/usr/bin/env bash
# Verify mailbox credentials by attempting SASL login to omv-ha postfix.
# Requires MAILBOX_PASSWORD environment variable.
set -euo pipefail

SMTP_HOST="${SMTP_HOST:-192.168.1.130}"
SMTP_PORT="${SMTP_PORT:-587}"
MAIL_FROM="${MAIL_FROM:-tbaltzakis@cloudless.gr}"

if [ -z "${MAILBOX_PASSWORD:-}" ]; then
  echo "ERROR: MAILBOX_PASSWORD environment variable is not set" >&2
  echo "Set it with: export MAILBOX_PASSWORD='your-password'" >&2
  exit 1
fi

echo "=== Mailbox Auth Check ==="
echo "Host: ${SMTP_HOST}:${SMTP_PORT}"
echo "User: ${MAIL_FROM}"
echo ""

python3 - "$SMTP_HOST" "$SMTP_PORT" "$MAIL_FROM" "$MAILBOX_PASSWORD" << 'PYEOF'
import sys
import smtplib

host = sys.argv[1]
port = int(sys.argv[2])
user = sys.argv[3]
password = sys.argv[4]

try:
    server = smtplib.SMTP(host, port, timeout=10)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(user, password)
    print("  AUTH: OK — mailbox credentials valid")
    server.quit()
    print()
    print("  Mailbox auth: OK")
except smtplib.SMTPAuthenticationError as e:
    print(f"  AUTH FAILED: {e}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"  ERROR: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
