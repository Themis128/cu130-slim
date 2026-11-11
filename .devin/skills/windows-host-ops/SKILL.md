---
name: windows-host-ops
description: >-
  Operate the Windows 11 host that runs WSL (Ubuntu-24.04) + Docker Desktop:
  DevOptimizer scheduled tasks, hidden/headless console execution (conhost
  --headless), WSL↔Windows interop popups and vsock flakiness, memory limits,
  and the popup-watch diagnostic. Use for PowerShell/conhost flashes, scheduled
  task maintenance, or WSL host-level instability.
---

# Windows Host Ops

The stack runs in WSL `Ubuntu-24.04` on a Windows 11 host (PowerShell 5.1 build
26100.9587, conhost supports `--headless`). Docker Desktop runs on the WSL backend.

## Scheduled maintenance tasks

Eleven tasks — `\DevOptimizer\` (elevated) + task-root (user-owned). All run
**headless** via `C:\Windows\System32\conhost.exe --headless <cmd>`:

| Task | Cadence | Action |
|---|---|---|
| `socialauto-metrics-watchdog` (root) | every 5 min | `wsl.exe -d Ubuntu-24.04 -e python3 .../metrics-proxy-watchdog.py` |
| `Docker WSL Integration Watchdog` (root) | every 5 min | `conhost --headless powershell ... C:\Users\tbaltzakis\scripts\Watch-DockerWslIntegration.ps1 -Mode Check` |
| `Docker-WSL-Sync` | 30 min + logon | `powershell ... sync-docker-wsl.ps1` |
| `WSL-Memory-Watchdog` | 30 min | `powershell ... check-wsl-memory-pressure.ps1` |
| `WSL-PreWarm` | logon | `wsl.exe -d Ubuntu-24.04 --exec true` |
| `Safe-Cleanup-Daily`, `WSL-Ubuntu-Cleanup`, `SocialAuto-Maintenance`, `Devin-Daily-Maintenance` | daily 02:15–04:30 | powershell scripts |
| `Admin-Cleanup-Weekly`, `Extended-Cleanup` | weekly | powershell scripts |

Scripts live at `D:\DevOptimizer\scripts\` (`/mnt/d/DevOptimizer/scripts/`).

## The empty-popup problem (fixed 2026-10-06, regressed + re-fixed 2026-10-23)

- `-WindowStyle Hidden` still **allocates a console** → brief empty conhost flash.
- Fix: launch via `conhost.exe --headless` — no console is ever created.
- Modifying `\DevOptimizer\` tasks needs elevation (UAC); root tasks are user-owned.
- **REGRESSION 2026-10-21**: `scripts/reregister-staggered-tasks.ps1` re-registered
  tasks with bare `powershell.exe` actions, stripping every headless wrap (~50
  flashes/day from the two 30-min tasks alone). Re-fixed 2026-10-23 by
  `scripts/_rewrap-headless.ps1` (enumerate `\DevOptimizer\` + root tasks, wrap
  non-conhost actions; run elevated — RunLevel=Highest tasks deny unelevated
  `Set-ScheduledTask`). The reregister script now has `New-HeadlessAction` —
  **any script that creates/registers task actions MUST wrap in
  `conhost.exe --headless`**; check for this whenever popups reappear.
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
(conhost --headless): `wsl --shutdown` → 15s → prewarm `Ubuntu-24.04` →
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

## Docker WSL Integration Watchdog + Telegram alerts (2026-11-02)

`C:\Users\tbaltzakis\scripts\Watch-DockerWslIntegration.ps1` probes
`wsl -d Ubuntu-24.04 --exec docker info` every 5 min. On failure it sends a
"repair starting" Telegram alert to the #ops channel (`TG_CH_OPS`,
`-1004429103353`), terminates + relaunches the distro, then sends
"repair complete" — or a FAILED alert if the distro stays unhealthy.

- `Send-Telegram` is **pure PowerShell** (`Invoke-RestMethod` -> Bot API) —
  deliberately no WSL/Docker dependency, since the failure it watches for can
  take WSL interop down with it. Token lookup: `TELEGRAM_BOT_TOKEN` env ->
  `C:\Users\tbaltzakis\cu130-slim\.env` -> `\\wsl.localhost\Ubuntu-24.04\...\.env`.
- Alert is best-effort with 3 retries; repair is NEVER blocked by alert
  delivery (a Telegram outage must not leave the stack broken).
- Retry policy: `Failed` state re-attempts repair after a 15-min cooldown;
  `Repairing` older than 10 min means the last run died mid-flight -> retry.
- Escalation: `consecutive-failures.txt` counts repair failures; the 2nd+
  consecutive failure also posts `[P1]` to the critical channel. Counter
  resets on recovery.
- `-Mode TestAlert` sends a one-shot verification message; `-Mode Install`
  re-registers the task (conhost --headless). Keep the script **pure ASCII**
  (PS 5.1 BOM-less-UTF8 em-dash parser trap — see above).
- Log/state: `%LOCALAPPDATA%\DockerWslIntegrationWatchdog\`.

### Failure classification (2026-11-02, retiered)

The probe is classified before acting so a transient vsock blip never triggers
a session-killing `wsl --terminate`:

- `Healthy` — exec runs, payload contains `Docker Desktop`.
- `Vsock` — output has `UtilAcceptVsock|accept4 failed|WSAETIMEDOUT|
  Wsl/Service|Wsl/ExecError`, or the bounded call times out, or `wsl.exe`
  fails to **spawn** (Start-Process throws during an interop wedge — was a
  silent-death gap, now returns TimedOut → vsock class).
- `Broken` — exec runs but docker.sock/proxy is dead → the original
  terminate-repair path, unchanged.

`Vsock` handling: vsock lives in the WSL utility VM — terminate cannot fix it.
The watchdog probes the engine via `docker.exe` (host npipe, vsock-independent);
runs `docker desktop restart` only if the engine is unreachable; increments
`vsock-flaps.txt`; writes state `VsockDegraded`; escalates `[P1]` to #critical
at flap 6 (~30 min) then every ~3h; defers the durable reset to the 04:45 task.
Recovery writes `Healthy` + "OK again (was VsockDegraded)". `-Mode TestVsock`
injects a synthetic vsock probe to exercise the branch end-to-end.

Live-incident hardening (19:08-19:26 flap cycle):
- `docker desktop restart` exits nonzero right after dispatching
  ("Starting Docker Desktop" + exit!=0, engine still comes back) — judge by
  dispatch text, not exit code.
- Engine restarts are throttled to one per 15 min (`ddrestart.out` timestamp);
  every vsock flap must not re-issue a heavyweight restart.
- Verified live end-to-end: flap -> engine-down detected -> restart dispatched
  -> DD `starting` 19:25 -> `running` 19:26 -> recovery alert, zero terminates.

### WSL Vsock Health Reset task (daily 04:45)

`C:\Users\tbaltzakis\scripts\Watch-VsockHealth.ps1` — probe-first scheduled
reset: 5× `wsl --exec docker info`; resets (`wsl --shutdown` → prewarm →
4-min verify loop) only on vsock-class degradation (>=1 UtilAcceptVsock-class
or >=3 down). Healthy/DockerDown verdicts exit quietly — never a blind reboot.
Telegram to #ops before and after the reset. `-Mode Check|Install|Uninstall|
TestAlert`, `-WhatIf` for dry runs. Log: `%LOCALAPPDATA%\VsockHealthWatchdog\`.

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

### Docker MCP gateway watchdog (every 10 min)

`C:\Users\tbaltzakis\devin-scripts\docker-mcp-watchdog.ps1` (source:
`scripts/docker-mcp-watchdog.ps1`), scheduled task `DockerMcpGateway-Watchdog`.

Failure class: `docker mcp gateway run` connects to
`\\.\pipe\dockerBackendApiServer` **once** at startup for its OAuth
notification monitor and never retries. If Docker Desktop's backend restarts
after the gateway, the monitor is permanently dead for that process lifetime
(log shows `Failed to connect to OAuth notifications ... connection refused`).
Cosmetic while `Authentication disabled`, but fatal for OAuth-based servers.

Logic: compares newest `com.docker.backend*` StartTime vs `docker-mcp`
StartTime; if backend is newer, probes the pipe, and restarts the gateway via
the `DockerMcpGateway` logon task only when the pipe accepts connections.
Also restarts the gateway if the process is simply gone. Log:
`%USERPROFILE%\logs\docker-mcp-watchdog.log`.
