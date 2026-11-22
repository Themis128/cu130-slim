#!/usr/bin/env bash
# tailnet-watch status/digest operator tool (WSL -> office-1 Windows).
set -euo pipefail
PS=(powershell.exe -NoProfile -NonInteractive -Command)
ROOT='D:\tailnet-watch'
TASK='\DevOptimizer-Tailnet-Watch-Status'

case "${1:-check}" in
  check)
    "${PS[@]}" "& python '$ROOT\scripts\tailnet-watch-status.py'" ;;
  history)
    "${PS[@]}" "Get-Content -Tail 24 '$ROOT\logs\status-history.jsonl' -ErrorAction SilentlyContinue;
      Write-Output '--- digest state ---';
      Get-Content '$ROOT\logs\status-digest.json' -ErrorAction SilentlyContinue;
      Write-Output '--- status.log tail ---';
      Get-Content -Tail 5 '$ROOT\logs\status.log' -ErrorAction SilentlyContinue" ;;
  task)
    "${PS[@]}" "Get-ScheduledTask -TaskPath '\' -TaskName '${TASK#\\}' | Get-ScheduledTaskInfo |
      Format-List LastRunTime,LastTaskResult,NextRunTime" ;;
  deploy)
    "${PS[@]}" "git -C '$ROOT' pull --ff-only; git -C '$ROOT' log --oneline -1;
      Select-String -Path '$ROOT\scripts\run-status-check.ps1' -Pattern '--digest' -SimpleMatch" ;;
  *)
    echo "usage: $0 {check|history|task|deploy}" >&2; exit 2 ;;
esac
