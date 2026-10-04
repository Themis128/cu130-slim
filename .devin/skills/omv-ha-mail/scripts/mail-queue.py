#!/usr/bin/env python3
"""Check mail queue on omv-ha postfix.
Requires SSH access to omv-ha (password or key).
Usage: mail-queue.py [--host 192.168.1.130] [--user tbaltzakis]"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env  # noqa: E402

SSH_HOST = env("SSH_HOST") or "192.168.1.130"
SSH_USER = env("SSH_USER") or "tbaltzakis"

i = 1
while i < len(sys.argv):
    if sys.argv[i] == "--host" and i + 1 < len(sys.argv):
        SSH_HOST = sys.argv[i + 1]
        i += 2
    elif sys.argv[i] == "--user" and i + 1 < len(sys.argv):
        SSH_USER = sys.argv[i + 1]
        i += 2
    else:
        print(f"Unknown option: {sys.argv[i]}", file=sys.stderr)
        sys.exit(1)

print(f"=== Mail Queue on omv-ha ({SSH_HOST}) ===\n")
remote_cmd = (
    "echo '--- Queue count ---'; sudo postqueue -p 2>/dev/null | tail -1; echo; "
    "echo '--- Queued messages ---'; sudo postqueue -p 2>/dev/null | head -20; echo; "
    "echo '--- Recent mail log ---'; sudo tail -20 /var/log/mail.log 2>/dev/null "
    "|| sudo journalctl -u postfix -n 20 --no-pager 2>/dev/null"
)
subprocess.run([
    "ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
    f"{SSH_USER}@{SSH_HOST}", remote_cmd])
print("\n=== Done ===")
