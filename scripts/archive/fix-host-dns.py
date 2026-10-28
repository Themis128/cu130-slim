#!/usr/bin/env python3
"""Optional host/WSL DNS fix when Tailscale MagicDNS cannot resolve public
names. Does NOT store your password. Run manually with sudo:
  sudo python3 scripts/fix-host-dns.py"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

if os.geteuid() != 0:
    print("Re-run with sudo: sudo python3 scripts/fix-host-dns.py",
          file=sys.stderr)
    sys.exit(1)

ts = time.strftime("%Y%m%d%H%M%S")
resolv = Path("/etc/resolv.conf")
daemon = Path("/etc/docker/daemon.json")

shutil.copy2(resolv, f"/etc/resolv.conf.bak.{ts}")
if daemon.exists():
    shutil.copy2(daemon, f"/etc/docker/daemon.json.bak.{ts}")

data = json.loads(daemon.read_text()) if daemon.exists() else {}
data["dns"] = ["1.1.1.1", "8.8.8.8"]
daemon.write_text(json.dumps(data, indent=4) + "\n")
print(f"Updated {daemon} dns={data['dns']}")

resolv.write_text("""\
# Public DNS first; MagicDNS kept as fallback for *.ts.net
# Backup saved next to this file as resolv.conf.bak.*
nameserver 1.1.1.1
nameserver 8.8.8.8
nameserver 100.100.100.100
search tail4ecae1.ts.net
""")

print("""Updated /etc/resolv.conf
Restart Docker so existing containers pick up daemon DNS:
  sudo systemctl restart docker
Then: cd /home/tbaltzakis/cu130-slim && docker compose up -d""")
