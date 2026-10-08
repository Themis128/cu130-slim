---
name: windows-host-ops
description: >-
  Operate the Windows 11 host that runs WSL (Ubuntu-26.04) + Docker Desktop:
  DevOptimizer scheduled tasks, hidden/headless console execution (conhost
  --headless), WSL↔Windows interop popups and vsock flakiness, memory limits,
  and the popup-watch diagnostic. Use for PowerShell/conhost flashes, scheduled
  task maintenance, or WSL host-level instability.
---

# Windows Host Ops

The stack runs in WSL `Ubuntu-26.04` on a Windows 11 host (PowerShell 5.1 build
26100.9587, conhost supports `--headless`). Docker Desktop runs on the WSL backend.

## Scheduled maintenance tasks

Ten tasks — `\DevOptimizer\` (elevated) + task-root (user-owned). All run
**headless** via `C:\Windows\System32\conhost.exe --headless <cmd>`:

| Task | Cadence | Action |
|---|---|---|
| `socialauto-metrics-watchdog` (root) | every 5 min | `wsl.exe -d Ubuntu-26.04 -e python3 .../metrics-proxy-watchdog.py` |
| `Docker-WSL-Sync` | 30 min + logon | `powershell ... sync-docker-wsl.ps1` |
| `WSL-Memory-Watchdog` | 30 min | `powershell ... check-wsl-memory-pressure.ps1` |
| `WSL-PreWarm` | logon | `wsl.exe -d Ubuntu-26.04 --exec true` |
| `Safe-Cleanup-Daily`, `WSL-Ubuntu-Cleanup`, `SocialAuto-Maintenance`, `Devin-Daily-Maintenance` | daily 02:15–04:30 | powershell scripts |
| `Admin-Cleanup-Weekly`, `Extended-Cleanup` | weekly | powershell scripts |

Scripts live at `D:\DevOptimizer\scripts\` (`/mnt/d/DevOptimizer/scripts/`).

## The empty-popup problem (fixed 2026-10-06)

- `-WindowStyle Hidden` still **allocates a console** → brief empty conhost flash.
- Fix: launch via `conhost.exe --headless` — no console is ever created.
- Modifying `\DevOptimizer\` tasks needs elevation (UAC); root tasks are user-owned.
- WSL→Windows interop calls (`powershell.exe -Command ...` from WSL) also flash a
  console because the WSL session has no Windows console attached — expected, keep
  such calls minimal; orphaned ones leave a stuck empty window (safe to close).
- Helper scripts on disk: `_hide-task-popups*.ps1`, `_popup-watch.ps1` (logs every
  powershell/cmd/conhost spawn with parent PID for N minutes).

## Known flakiness

- **vsock degradation**: `wsl.exe`/interop calls intermittently fail with
  `UtilAcceptVsock`/`accept4` errors; tasks may log `0x800704E0` once and recover.
  Persistent breakage → `wsl --shutdown` (kills Docker Desktop + all sessions — plan it).
- **Docker Desktop bounces** under memory pressure: WSL capped at ~19–20GB,
  dropcache reclaim, 16GB swap — watch `WSL-Memory-Watchdog` results.
- **Docker-WSL-Sync resurrects sleepers**: the task starts every Exited
  `unless-stopped` container every 30min — it now queries stack-ops
  `/status` (localhost:8787) and skips managed containers. If stack-ops is
  down the script falls back to old resurrect-all behavior (safe: nothing
  is sleeping then anyway). Managed containers that crash get re-woken by
  the proxy on next request instead of by this task.
- **Resource Saver**: no `EnableResourceSaver` key in
  `%APPDATA%\Docker\settings-store.json` → default ON. Resource Saver + WSL
  `autoMemoryReclaim=gradual` is a documented freeze combo; the host runs
  `dropCache` which is the safe pairing. Do NOT switch `.wslconfig` back to
  `gradual` (host-starvation freezes observed); if freezes return, disable
  Resource Saver in Docker Desktop Settings → Resources instead. Idle
  container memory is handled in-stack by `stack-ops` (idle-sleep proxy).
- Non-task console spawns are NOT fixable via tasks: Chrome extension
  native-messaging (`cmd.exe` from McAfee WebAdvisor etc.), Devin/Windsurf's own
  `wsl.exe`, Lenovo Vantage. Disable the extension if a flash traces to it.

## Verifying task changes

```powershell
Get-ScheduledTask -TaskPath '\DevOptimizer\' | % { $_.Actions[0].Execute + ' ' + $_.Actions[0].Arguments }
(Get-ScheduledTask -TaskName X).TaskInfo.LastTaskResult   # 0 = ok
```

`0x41301` = still running; `0x800704E0` = wsl.exe couldn't reach the VM (vsock).
