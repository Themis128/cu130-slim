#!/usr/bin/env bash
# Test SMTP connectivity to omv-ha postfix.
# Checks port 587 (submission) and verifies STARTTLS + EHLO response.
set -euo pipefail

SMTP_HOST="${SMTP_HOST:-192.168.1.130}"
SMTP_PORT="${SMTP_PORT:-587}"

echo "=== SMTP Connectivity Test ==="
echo "Target: ${SMTP_HOST}:${SMTP_PORT}"
echo ""

# Check TCP connectivity
if nc -zv -w 5 "$SMTP_HOST" "$SMTP_PORT" 2>&1; then
  echo "  TCP: OK"
else
  echo "  TCP: FAILED — cannot reach ${SMTP_HOST}:${SMTP_PORT}"
  echo "  Check: Is omv-ha online? Is the postfix service running?"
  exit 1
fi

echo ""

# Check SMTP banner and STARTTLS
python3 - "$SMTP_HOST" "$SMTP_PORT" << 'PYEOF'
import sys
import smtplib

host = sys.argv[1]
port = int(sys.argv[2])

try:
    server = smtplib.SMTP(host, port, timeout=10)
    code, msg = server.ehlo()
    print(f"  EHLO: OK ({code} {msg.decode()[:80]})")

    # Check STARTTLS
    if server.has_extn('starttls'):
        print("  STARTTLS: Available")
        server.starttls()
        server.ehlo()
        print("  STARTTLS: OK (negotiated)")
    else:
        print("  STARTTLS: NOT available — WARNING")

    # Check AUTH
    if server.has_extn('auth'):
        print("  AUTH: Available (SASL)")
    else:
        print("  AUTH: NOT available")

    # Check size limit
    if server.has_extn('size'):
        size = server.esmtp_features.get('size', 'unknown')
        print(f"  SIZE limit: {size} bytes")

    server.quit()
    print()
    print("  SMTP connectivity: OK")
except Exception as e:
    print(f"  SMTP test FAILED: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
