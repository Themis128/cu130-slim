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
- **SocialAuto-Maintenance deleted sleeping containers (2026-10-08)**:
  `socialauto/Maintain-SocialAuto.ps1` ran `docker container prune -f` at
  04:00 and wiped ALL 16 idle-sleeping containers (ComfyUI, all sidecars,
  LanguageTool, etc.) — stack-ops wake then fails with "No such container"
  and the only recovery is `docker compose up -d <svc>` (containers are
  recreated; browser-profile volumes survive so sessions persist).
  The script now prunes with `--filter "label!=com.docker.compose.project=cu130-slim"
  --filter "label!=com.docker.desktop.service=model-runner"` and counts
  prunable containers by subtracting protected IDs (`docker ps` does NOT
  support `label!=`; `container prune` does). The other three prune
  scripts (`dev-cache-cleaner`, `dev-cleanup`, `wsl-deep-clean`) already
  carry the DMR label exclusions but are not scheduled — watch for them
  if they ever get a task.
- **PowerShell gotcha**: `Maintain-SocialAuto.ps1` runs under Windows
  PowerShell 5.1, which reads BOM-less UTF-8 as ANSI — a UTF-8 em-dash
  (`—`) decodes to `â€”` where `”` IS a string terminator for the PS
  parser → "Missing closing '}'" syntax errors. Keep these scripts pure
  ASCII (no em-dashes/smart quotes).
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

## GPU-P breakage after driver install (2026-10-17)

Signature: Windows `nvidia-smi.exe` healthy but WSL `nvidia-smi` reports
`Failed to initialize NVML: GPU access blocked by the operating system`,
containers see `torch.cuda.is_available() == False`, and dmesg shows
`dxgk: dxgvmb_send_create_process: create_process failed -75`.

Cause: an NVIDIA driver install/hot-swap **while the utility VM is running**
leaves the VM's dxg channel bound to the pre-install driver instance. There
is no in-VM fix — the GPU-P channel is per-VM and only rebinds on VM start.

Fix: bounce WSL from the Windows side. Registered task
`\DevOptimizer\WSL-GPU-Rebind` runs `D:\DevOptimizer\scripts\wsl-gpu-rebind.ps1`
(conhost --headless): `wsl --shutdown` → 15s → prewarm `Ubuntu-26.04` →
runs `scripts/wsl-gpu-verify.sh` in-VM (waits for Docker, checks
`nvidia-smi`, probes `torch.cuda` inside `social-media-comfyui-gpu`, and
force-recreates the container if it still lacks the dxg mapping — a
container *created* while GPU-P was down keeps its stale mapping).

Trigger from Windows: `Start-ScheduledTask '\DevOptimizer\WSL-GPU-Rebind'`.
WARNING: kills every WSL session (including Devin/IDE sessions in WSL) and
all containers — Docker restart policies bring the stack back
(verified: 35/36 containers auto-recover on a bounce).

## Verifying task changes

```powershell
Get-ScheduledTask -TaskPath '\DevOptimizer\' | % { $_.Actions[0].Execute + ' ' + $_.Actions[0].Arguments }
(Get-ScheduledTask -TaskName X).TaskInfo.LastTaskResult   # 0 = ok
```

`0x41301` = still running; `0x800704E0` = wsl.exe couldn't reach the VM (vsock).

## Docker Desktop backend death (2026-10-18)

Signature: `docker version` shows client but **no server version**, every
`docker` call hangs, `/var/run/docker.sock` exists, and
`wsl.exe -l -v` shows `docker-desktop` distro **Stopped** while
`docker desktop status` claims `running` — the backend VM died without
Docker Desktop noticing.

Fix (works from WSL, no shutdown needed):

```powershell
& 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' desktop restart
```

~20s later the engine answers and containers come back on their restart
policies. Postgres needs ~30s extra — worker tasks that land in the gap
fail with `the database system is starting up`; retry them.
