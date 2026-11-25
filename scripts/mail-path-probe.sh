#!/usr/bin/env bash
# mail-path-probe.sh — SMTP submission-path canary for the whole stack.
#
# Runs inside compose-healer every 600s alongside compose-converge.sh
# (docker:27-cli + docker.sock). Verifies, from inside social-api:
#   1. mail.cloudless.gr resolves to a private/CGNAT address — catches both
#      stale public DNS (dead WAN IP) and a missing extra_hosts pin.
#   2. TCP 587 reachable on omv-ha postfix.
#   3. STARTTLS handshake + certificate hostname verification (the pin must
#      keep TLS fully validated — no SSL_VERIFY bypass).
#
# On failure alerts the Telegram ops lane via send_telegram_alert (built-in
# 6h dedup). Always exits 0 — a probe must never crash the healer loop.
set -u

probe() {
  docker exec -i social-api python - <<'PY' 2>/dev/null
import ipaddress
import os
import smtplib
import socket
import ssl
import sys

host = os.environ.get("SMTP_HOST", "mail.cloudless.gr")
port = int(os.environ.get("SMTP_PORT", "587"))
try:
    ip = socket.gethostbyname(host)
except Exception as e:
    print(f"DNS-FAIL {host}: {e}")
    sys.exit(2)
try:
    addr = ipaddress.ip_address(ip)
    private = addr.is_private or addr.is_loopback or addr.is_link_local or str(addr).startswith("100.")
except Exception:
    private = False
if not private:
    print(f"STALE-DNS {host} -> {ip} (public address; extra_hosts pin not applied)")
    sys.exit(3)
try:
    s = smtplib.SMTP(host, port, timeout=10)
    s.ehlo()
    s.starttls(context=ssl.create_default_context())
    s.ehlo()
    s.sock.getpeercert()
    s.quit()
except Exception as e:
    print(f"SMTP-FAIL {host}:{port} -> {type(e).__name__}: {e}")
    sys.exit(4)
print(f"OK {host} -> {ip}:{port} TLS-verified")
PY
}

out=$(probe)
rc=$?
echo "mail-probe: $out (rc=$rc)"

if [ "$rc" -ne 0 ]; then
  docker exec -i -e PROBE_OUT="$out" social-api python - <<'PY' 2>/dev/null
import asyncio
import os
import sys

sys.path.insert(0, "/app")
from app.services.slack_notifications import send_telegram_alert

asyncio.run(send_telegram_alert(
    "ops",
    "MAIL-PATH DOWN: " + os.environ.get("PROBE_OUT", "probe failed")
    + " — stack email (campaigns, digests, ops alerts) is failing."
    " Check mail.cloudless.gr extra_hosts pin + omv-ha postfix :587.",
))
PY
fi
exit 0
