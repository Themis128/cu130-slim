#!/usr/bin/env bash
# omv_pull_watchdog.sh — pull-model: Windows/WSL polls omv for commands
# instead of omv SSH-ing into the host (kills the Windows-sshd gate).
# Runs via schtasks task "OmVPull" every minute.
cd "$HOME/cu130-slim" || exit 1
mkdir -p logs
exec 9>/tmp/omv_pull.lock && flock -n 9 || exit 0
CMDS=$(ssh -o ConnectTimeout=8 -o BatchMode=yes tbaltzakis@100.74.191.58 \
  'cat /tmp/win_cmds.sh 2>/dev/null && rm -f /tmp/win_cmds.sh' 2>/dev/null) || exit 0
[ -z "$CMDS" ] && exit 0
echo "=== $(date -u +%FT%TZ) received ===" >> logs/omv-pull.log
echo "$CMDS" >> logs/omv-pull.log
while IFS= read -r line; do
  [ -z "$line" ] && continue
  case "$line" in
    wsl:*)   bash -lc "${line#wsl:}"   >> logs/omv-pull.log 2>&1 ;;
    *)       /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe \
               -NoProfile -Command "$line" >> logs/omv-pull.log 2>&1 ;;
  esac
  echo "--- rc=$? ---" >> logs/omv-pull.log
done <<< "$CMDS"
