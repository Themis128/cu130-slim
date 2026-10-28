#!/usr/bin/env python3
"""Test SMTP connectivity to omv-ha postfix.
Checks port 587 (submission) and verifies STARTTLS + EHLO response."""

import smtplib
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env  # noqa: E402

SMTP_HOST = env("SMTP_HOST") or "192.168.1.130"
SMTP_PORT = int(env("SMTP_PORT") or 587)

print("=== SMTP Connectivity Test ===")
print(f"Target: {SMTP_HOST}:{SMTP_PORT}\n")

try:
    socket.create_connection((SMTP_HOST, SMTP_PORT), timeout=5).close()
    print("  TCP: OK")
except OSError:
    print(f"  TCP: FAILED — cannot reach {SMTP_HOST}:{SMTP_PORT}")
    print("  Check: Is omv-ha online? Is the postfix service running?")
    sys.exit(1)

print()
try:
    server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
    code, msg = server.ehlo()
    print(f"  EHLO: OK ({code} {msg.decode()[:80]})")
    if server.has_extn("starttls"):
        print("  STARTTLS: Available")
        server.starttls()
        server.ehlo()
        print("  STARTTLS: OK (negotiated)")
    else:
        print("  STARTTLS: NOT available — WARNING")
    print("  AUTH: Available (SASL)" if server.has_extn("auth")
          else "  AUTH: NOT available")
    if server.has_extn("size"):
        print(f'  SIZE limit: {server.esmtp_features.get("size", "unknown")} bytes')
    server.quit()
    print("\n  SMTP connectivity: OK")
except Exception as e:
    print(f"  SMTP test FAILED: {e}", file=sys.stderr)
    sys.exit(1)
