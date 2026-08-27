#!/usr/bin/env bash
# Optional host/WSL DNS fix when Tailscale MagicDNS cannot resolve public names.
# Does NOT store your password. Run manually: bash scripts/fix-host-dns.sh
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Re-run with sudo: sudo bash scripts/fix-host-dns.sh" >&2
  exit 1
fi

ts="$(date +%Y%m%d%H%M%S)"
cp -a /etc/resolv.conf "/etc/resolv.conf.bak.${ts}"
if [[ -f /etc/docker/daemon.json ]]; then
  cp -a /etc/docker/daemon.json "/etc/docker/daemon.json.bak.${ts}"
fi

python3 - <<'PY'
import json
from pathlib import Path
p = Path("/etc/docker/daemon.json")
data = json.loads(p.read_text()) if p.exists() else {}
data["dns"] = ["1.1.1.1", "8.8.8.8"]
p.write_text(json.dumps(data, indent=4) + "\n")
print("Updated", p, "dns=", data["dns"])
PY

cat >/etc/resolv.conf <<'EOF'
# Public DNS first; MagicDNS kept as fallback for *.ts.net
# Backup saved next to this file as resolv.conf.bak.*
nameserver 1.1.1.1
nameserver 8.8.8.8
nameserver 100.100.100.100
search tail4ecae1.ts.net
EOF

echo "Updated /etc/resolv.conf"
echo "Restart Docker so existing containers pick up daemon DNS:"
echo "  sudo systemctl restart docker"
echo "Then: cd /home/tbaltzakis/ComfyUI-Docker/cu130-slim && docker compose up -d"
