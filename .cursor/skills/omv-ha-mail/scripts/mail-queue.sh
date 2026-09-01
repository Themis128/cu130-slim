#!/usr/bin/env bash
# Check mail queue on omv-ha postfix.
# Requires SSH access to omv-ha (password or key).
# Usage: mail-queue.sh [--host 192.168.1.130] [--user tbaltzakis]
set -euo pipefail

SSH_HOST="${SSH_HOST:-192.168.1.130}"
SSH_USER="${SSH_USER:-tbaltzakis}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) SSH_HOST="$2"; shift 2 ;;
    --user) SSH_USER="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

echo "=== Mail Queue on omv-ha (${SSH_HOST}) ==="
echo ""

ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "${SSH_USER}@${SSH_HOST}" \
  "echo '--- Queue count ---'; sudo postqueue -p 2>/dev/null | tail -1; echo; echo '--- Queued messages ---'; sudo postqueue -p 2>/dev/null | head -20; echo; echo '--- Recent mail log ---'; sudo tail -20 /var/log/mail.log 2>/dev/null || sudo journalctl -u postfix -n 20 --no-pager 2>/dev/null" 2>&1

echo ""
echo "=== Done ==="
