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
| `DD-Settings-Watchdog` (`\DevOptimizer\`) | every 5 min | `dd-settings-watchdog.ps1` — see below |

## DD-Settings-Watchdog (Docker Desktop settings drift)

Guards `%APPDATA%\Docker\settings-store.json` (Docker Desktop ≥4.35 renamed
`settings.json` → `settings-store.json`; the legacy file may still exist but
is ignored by modern DD). Desired state: `IntegratedWslDistros` contains
`Ubuntu-24.04` + `EnableIntegrationWithDefaultWslDistro=true` — losing the
Ubuntu-24.04 integration entry kills `docker.sock` for the whole stack.

**2026-11-13 incident:** the old script read the deleted `settings.json`,
false-positived drift on every 5-min run, and `Restore-Settings` restarted
Docker Desktop **every pass** — the restart loop drove the vsock flap /
engine outage. It also enforced `daemon.userns-remap` by writing to
`Ubuntu-24.04:/etc/docker/daemon.json`, a distro DD never reads (and
userns-remap is unsupported on the DD WSL2 backend — docker/for-win#6897).

Rewritten contract (devoptimizer PR #15):
- drift detection only on the store schema; legacy file = detect-only,
  never auto-restored
- auto-restore rewrites `settings-store.json`, then restarts DD **only if
  the file changed**, max once per **6h** (`lastRestart` in state file)
- alerts once per drift episode + 24h reminder; resolved-notice on recovery
- state: `D:\DevOptimizer\logs\dd-settings-watchdog-state.json`
- log: `D:\DevOptimizer\logs\dd-settings-watchdog.log`

Check it from WSL (no powershell needed — /mnt/c + /mnt/d are direct):

```bash
python3 .devin/skills/windows-host-ops/scripts/dd_settings_check.py   # status
python3 .devin/skills/windows-host-ops/scripts/dd_settings_check.py --json
```

Exits non-zero on drift, stale watchdog (>15min since lastCheck), or missing
files. Deployed copy lives at `D:\DevOptimizer\scripts\` — the scheduled
task runs THAT path, not the WSL clone; after editing
`/home/tbaltzakis/devoptimizer/scripts/dd-settings-watchdog.ps1` copy it to
`/mnt/d/DevOptimizer/scripts/` (and commit via PR — repo is
`Themis128/devoptimizer`).

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
  powershell/cmd/conhost spawn with parent PID for N minutes), and
  `popup_task_audit.ps1` (repo: `scripts/popup_task_audit.ps1`, also copied to
  `D:\DevOptimizer\scripts\`).

### Popup audit & repair tool (2026-11-13)

`scripts/popup_task_audit.ps1` — one-shot audit of every scheduled task action:

```powershell
# audit only (default): lists FLASH-RISK actions (user tasks; \Microsoft\* vendor
# tasks excluded - they run as SYSTEM and manage their own consoles)
powershell -NoProfile -ExecutionPolicy Bypass -File popup_task_audit.ps1
# repair in an already-elevated shell
powershell -NoProfile -ExecutionPolicy Bypass -File popup_task_audit.ps1 -Fix
# repair WITHOUT elevation: runs through the PopupTaskAudit-Fix helper task
# (RunLevel=Highest, registered once via -RegisterElevatedHelper; output in
# %LOCALAPPDATA%\PopupTaskAudit\fix.log). schtasks /run needs no UAC.
powershell -NoProfile -ExecutionPolicy Bypass -File popup_task_audit.ps1 -FixElevated
# one-time helper registration - MUST be run from an elevated shell once
powershell -NoProfile -ExecutionPolicy Bypass -File popup_task_audit.ps1 -RegisterElevatedHelper
# machine-readable / vendor-inclusive
powershell -NoProfile -ExecutionPolicy Bypass -File popup_task_audit.ps1 -Json -IncludeVendor
```

Elevation notes (researched 2026-11-13): native `sudo.exe` exists on this host
(Win11 24H2) but is DISABLED and still UAC-prompts per call - not unattended.
gsudo needs an interactive first UAC + ~5min cache - same problem. The
privileged-helper-task pattern (`schtasks /run` on a RunLevel=Highest task) is
the standard zero-UAC lane; `PopupTaskAudit-Fix` is the registered instance.

Rules baked in: `-WindowStyle Hidden` counts as FLASH-RISK (console still
allocated); `conhost` without `--headless` is repaired by prepending the flag,
never double-wrapped; GUI-subsystem exes and `pythonw`/`wscript` are OK.
Run it after ANY script that registers task actions, and whenever popups
reappear — the 2026-10-21 regression came from `reregister-staggered-tasks.ps1`
stripping wraps (it now uses `New-HeadlessAction`).

Known non-task flash sources (audited + remediated 2026-11-14):

- **Docker Desktop stats polling** — `com.docker.backend` spawns
  `cmd.exe /d /s /c "docker stats --all --no-trunc --no-stream --format
  "{{ json .}}""` every ~5 s whenever containers run, each allocating a
  conhost → visible flash. Upstream bug **docker/for-win#13923 / #13967**
  (open on DD 4.99.0, the latest): NO setting disables it. Mitigation:
  scheduled task **`DockerDashboardGuard`** (*/5min, `conhost --headless`)
  runs `D:\DevOptimizer\scripts\docker-dashboard-guard.ps1` which
  `CloseMainWindow()`s any visible Docker Desktop window — engine keeps
  running in tray. If `docker stats` flashes persist at high rate, check
  `_docker-dashboard-guard.log` for whether DD keeps reopening it.
  Watch signature: `parent=docker.exe(...) cmd=...stats --all
  --no-trunc --no-stream...`.
- **WSL cron/`adb.exe`/`docker.exe` interop spawns** — every Windows
  interop call from WSL cron flashes a console. Fixed 2026-11-14: home-ops
  watchdog/mcp-heal/image-sync/recovery prefer bare `docker` when
  `docker info --format '{{.Name}}'` returns `docker-desktop`
  (`dd-user-distro-proxy.service` path); `phone_fb_approve.py`,
  `groqbot-device-check.sh`, `phone-adb-watch.sh` prefer
  `~/.local/platform-tools/adb` (WSL-native adb server coexists with the
  Windows one — adbd accepts a transport per host; watch mirrors
  ip:port endpoints). User crontab PATH now includes
  `/home/tbaltzakis/.local/bin`. New scripts: use the same
  verified-name pattern — see home-ops `AGENTS.md`.
- **McAfee WebAdvisor extension** — REMOVED 2026-11-14 (Chrome ext id
  `fheoggkfdfchfphceeifdbepaooicaho`; Edge was clean; not installed as a
  program, no native-messaging host). chrome:// pages are unreachable
  via CDP on the default profile and HKCU Chrome policy writes are
  ACL-denied, so removal was automated via UI Automation:
  `D:\DevOptimizer\scripts\chrome-ext-remove.ps1 -ExtensionId <id>`
  (focuses a visible Chrome window, omnibox →
  `chrome://extensions/?id=<id>`, invokes Remove + confirm). Verify in
  `Secure Preferences` → `extensions.settings` (~5s flush lag).

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
  `%APPDATA%\Docker\settings-store.json` → default ON. NOTE (2026-11-11):
  `.wslconfig` now runs `autoMemoryReclaim=gradual` — the 2026-11-07
  stability fix reverted the earlier dropCache choice after dropCache
  correlated with WSL VM crashes. Do NOT flip back to dropCache. If
  Resource Saver + gradual freezes return, disable Resource Saver in
  Docker Desktop Settings → Resources instead. Idle container memory is
  handled in-stack by `stack-ops` (idle-sleep proxy).
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

## Non-task popup sources — the 2026-11-17 remediation

A clean `popup_task_audit.ps1` (178 tasks, risky=0) does NOT mean popups are
gone. The remaining flash classes live outside the task layer:

### 1. Bare WSL→Windows interop calls (the big one)

Every `powershell.exe` / `cmd.exe` / `docker.exe` / `adb.exe` invoked from WSL
allocates a conhost on the interactive desktop → empty-cmd flash. `-WindowStyle
Hidden` does not help (console is allocated before the style applies).

Fix — `~/scripts/win-hidden.sh`, sourced by every WSL cron/watch script:

```bash
winrun <exe> [args...]   # any Windows exe, /mnt/c or C:\ path — no console
winps  [args...]         # powershell.exe via the same lane
wincmd /c <args...>      # cmd.exe (e.g. `start "" app.exe`)
```

It spawns `pythonw.exe` (GUI subsystem → no console) running
`C:\Users\tbaltzakis\devin-scripts\mcp-hide.pyw`, which creates the child with
`CREATE_NO_WINDOW` and passes stdin/stdout/stderr/exit-code through — safe for
output-capturing AND stdio-protocol (MCP) calls.

Patched callers: `social-stack-watchdog.sh`, `wsl-vsock-autoheal.sh`,
`c-drive-watch.sh` (vhdx-compact), `groqbot-device-check.sh`,
`phone-adb-watch.sh` (adb.exe fallback), `mcp-toolkit-heal.sh` (all `docker mcp`
calls + the generated `~/.local/bin/docker-mcp-gateway` wrapper),
`mcp-health-watch.sh` (Windows-docker fallback probe),
`mail-outbound-secret-heal.sh` (secrets via temp files + Windows-side
`mail-outbound-secret-put.ps1` under `conhost --headless` — the inner
`npx.cmd wrangler` grandchild would otherwise flash).

### 2. conhost --headless is for fire-and-forget only

`conhost.exe --headless <cmd>` suppresses the window but emits terminal escape
sequences and eats stdout — NEVER wrap output-capturing calls in it. Use it for
scheduled-task actions and detached children (`winrun conhost --headless
powershell -File ...`); use `mcp-hide.pyw`/`winrun` for everything else.

### 3. The grandchild rule

Wrapping the outer process is not enough — a console-subsystem grandchild
allocates its own console. Real offenders found 2026-11-17:
`pythonw → pwsh -WindowStyle Hidden -Command 'npx …'` (the npx `.cmd` shim
flashed), `node.exe → cmd.exe /d /s /c playwright-mcp`, and
`pwsh -Command "… | npx.cmd …"`. Fixes: install the package globally and point
the config at the resolved `.js` entrypoint (verified: `@playwright/mcp` →
`cli.js`, `@softeria/ms-365-mcp-server` → `dist/index.js`, `firebase-tools` →
`lib/bin/firebase.js`), or wrap the inner chain in `conhost --headless` /
`Start-Process -NoNewWindow`.

### 4. MCP client configs

Both `C:\Users\tbaltzakis\AppData\Roaming\Devin\mcp_config.json` and
`C:\Users\tbaltzakis\.zcode\mcp_servers.json` were rewritten 2026-11-17 to route
every console-app spawn through `mcp-hide.pyw` (or direct `.js` entrypoints).
Remote-URL MCP entries (context7, github, gitlab, linear, greptile) spawn
nothing local — leave them. `bun`/`php` entries were dead weight (binaries not
installed). Keep future entries wrapped; check new MCP installs against this.

### 5. Inherent / won't-fix flashes

- Devin's own exec channel (`wsl.exe`/`bash.exe`/`wslhost.exe` per command) —
  platform behavior.
- Chrome native-messaging hosts (`chrome.exe → cmd /c …NativeHost`) — spawned
  by browser extensions; remove the extension to remove the flash.
- `docker stats`-family backend polling — Docker Desktop internal.

### Diagnostic loop

```powershell
# 1) tasks clean?  (source of truth for the task layer only)
powershell -File popup_task_audit.ps1
# 2) what's ACTUALLY spawning consoles — run from a Windows working dir,
#    NOT a \\wsl UNC cwd (cmd prints the UNC warning and a flash itself)
D:\DevOptimizer\scripts\_popup-watch.ps1   # logs parent→child cmdlines
# 3) stale logs lie — always diff against a fresh capture before attributing
```

### Battle-test results (2026-11-17 evening)

Fresh `_popup-watch` capture after remediation, exercising every lane:

- `winrun`/`winps`/`wincmd` verified: `docker.exe version`, `docker mcp
  profile list`, `docker mcp secret ls`, `adb.exe devices`, powershell and
  `cmd /c` all return output with `CREATE_NO_WINDOW` — their `conhost 0x4`
  children are the hidden WSL-pty console, not desktop flashes.
- A fresh `conhost 0x4` IS still a flash when its parent chain is
  `pwsh/powershell -WindowStyle Hidden → npx/.cmd` — the wrapper shields the
  outer process but a console-app grandchild allocates a visible console.
- **MCP clients cache their config** — after rewriting `mcp_config.json` /
  `mcp_servers.json`, running Devin/Windsurf/ZCode processes keep respawning
  MCP servers with the OLD `pwsh -Command 'npx -y …'` argv until the client
  is restarted. The 23:20 capture proved this (`npx -y @playwright/mcp`
  respawns while the on-disk config already pointed at `cli.js`).
- Additional WSL-side flash callers found and patched: `cdp-debug-watch.py`
  (cron */10 — `_winps()` helper now wraps all 4 powershell call sites),
  `phone_fb_approve.py` (cron */2), `phone_exitnode.py`, `phone_net_diag.py`
  (`_a()` helper wraps the `adb.exe` fallback only — Linux adb is preferred).
- `startup-hidden/*.vbs` launchers (`WScript.Shell.Run ...,0,`) spawn
  `cmd /c *.bat` — conhost exists but the window is hidden; NOT a flash.
- `node.exe → cmd` vitest-extension workers and `chrome → cmd NativeHost`
  spawns are client-internal; out of scope.
- `wsl-vsock-autoheal` live-diagnosed `proxy-dead` during the battle test —
  3-consecutive-check escalation auto-respawns the proxy; healthy behavior.

### 6. Windows-side grandchildren of headless tasks (2026-11-22)

`conhost --headless` wraps the PARENT only: a console-subsystem grandchild
spawned inside the script (`wsl.exe`, `adb.exe`, `tailscale.exe`, `tasklist.exe`,
`Start-Job`'s pwsh, `Start-Process -WindowStyle Hidden`) still allocates a
real console → flash, every pass. `_popup-watch` shows them as
`conhost 0x4` children of the headless script's pwsh.

Fixes applied (pattern: spawn with `CREATE_NO_WINDOW`, never bare `&`):

- **`Watch-DockerWslIntegration.ps1`** — `Start-Process wsl.exe -WindowStyle
  Hidden` → `-NoNewWindow` (attaches to the headless parent console).
- **`repair-wsl-docker-integration.ps1`** `WslArgs` — `& wsl.exe` →
  `ProcessStartInfo{CreateNoWindow=$true}` + ArgumentList (no quoting pitfalls).
- **`phone-tailnet-watch.ps1`** — `Start-Job` (allocates a console for the job
  pwsh) + bare `tailscale.exe`/`& $Adb` calls → shared `Invoke-Hidden` helper
  (ProcessStartInfo, CREATE_NO_WINDOW, capture stdout, timeout+kill).
- **`docker-socket-watch.bat`** — `wsl.exe` calls →
  `pythonw.exe mcp-hide.pyw wsl.exe …` (exit code passthrough keeps
  `if errorlevel` working).
- **`wsl-resume-watch.ps1`** — all four `& wsl.exe` calls → `Invoke-Wsl`
  helper (same CREATE_NO_WINDOW pattern).
- **`gmail-telegram-watch/run-all-watches.bat`** — the bat itself ran
  `python.exe` (console app) as its grandchild → `conhost 0x4` every ~10 min
  even under `conhost --headless wscript → vbs` parent. Bat now calls
  `pythonw.exe`; `\DevinGmailWatch` action simplified to
  `conhost --headless cmd.exe /c run-all-watches.bat` (the vbs layer was
  redundant — it hid the cmd window but could never stop the grandchild's
  own console).
- **`wsl.exe -e docker info` inside `cdp-relay-watch.ps1`** (spawned under
  `conhost --headless chrome-launch-watch.bat`) allocated its own
  `conhost 0x4` — same grandchild rule: inner Windows-console calls in
  headless scripts need `CREATE_NO_WINDOW`/pythonw, never bare `wsl.exe`.

### 7. Windows Terminal as default console host (2026-11-22)

When `HKCU\Console\%%Startup` is absent ("Let Windows decide"), Windows
Terminal handles every console hand-off. Two consequences (upstream
microsoft/terminal#12464):

- `wsl.exe -d <distro>` spawns **wslhost.exe with its own console**; under WT
  the hand-off puts that console on screen → flash CREATE_NO_WINDOW cannot
  reach (it belongs to wslhost, not the caller).
- Bare console-app spawns (old tasks, IDE children) show WT windows/tabs
  instead of conhost flashes.

Fix: pin delegation to conhost — create `HKCU\Console\%%Startup` with
`DelegationConsole` + `DelegationTerminal` =
`{00000000-0000-0000-0000-000000000000}`. wslhost consoles then stay hidden;
WT still works as an app. This host is pinned as of 2026-11-22.

## Memory architecture + pressure automation (2026-11-27)

Full audit of the 32GB RAM constraint on OFFICE — the documentation of what
exists, what is tuned, and where the ceiling is.

### Hardware ceiling

- Lenovo 82RF (LNVNB161216), i9-12900H, **2 slots, both occupied** by
  16GB DDR5-4800 Micron SODIMMs (MTC8C1084S1SC48BA1). 32GB is Lenovo's
  validated max; the CPU supports 64GB. The upgrade path is
  **2x32GB DDR5-4800 SODIMM replacing both sticks** (e.g. Crucial
  CT2K32G48C40S5) — no free slots exist.
- Structural demand: host baseline ~19-20GB + WSL fleet wants ~24GB
  (measured: 9GB physical cap + ~15GB swap in use inside the VM). With 32GB
  total the machine is permanently oversubscribed; software can only absorb
  pressure, never remove it.

### The guard stack (all live, all automatic — never duplicate it)

| Guard | Cadence | Escalation ladder |
|---|---|---|
| `DevOptimizer-DMR-RAM-Guard` | 5 min | free<2.8GB evicts non-pinned DMR models; <1.0GB evicts pinned too |
| `\DevOptimizer\Host-Guard` | 5 min | avail<2.5GB guest drop_caches; VRAM>85% model unload; <1.2GB critical: bounce WSL/Docker unless Devin.exe open |
| `\DevOptimizer\WSL-Memory-Watchdog` | 30 min | swap 80% warn / 90% cache-drop / 95% restart docker-desktop distro (2h cooldown) |
| `DockerEngineRescue` | 3 min | dead engine probe -> `wsl --shutdown` -> relaunch DD -> verify (15-min debounce) |

Scripts live in `D:\DevOptimizer\scripts\` (host-guard.ps1, dmr-ram-guard.ps1,
check-wsl-memory-pressure.ps1, wsl-guardian.ps1) and
`C:\Users\tbaltzakis\devin-scripts\` (docker-engine-rescue.ps1).
**Before writing any new memory/pressure automation, read these first** —
a parallel governor was mistakenly started 2026-11-27 and deleted; the
existing ladder already covers every tier.

### .wslconfig state (C:\Users\tbaltzakis\.wslconfig)

- `memory=9GB` — the documented optimum for 32GB. History: 14->20->14->12->10->9,
  each raise re-starved the host (0.3-1.2GB free crises). **Do not raise the
  cap on 32GB** — raising it re-creates the starvation crashes.
- `swap=32GB` on `D:\wsl-sys\swap.vhdx`; `processors=8`;
  `autoMemoryReclaim=gradual` (dropCache caused 4 engine crashes 2026-11-07);
  `sparseVhd=true` (docker_data.vhdx auto-trims after prunes — never compact
  it manually, it oscillates 182-230GB by design);
  `vmIdleTimeout=-1` (idle shutdown killed IDE sessions); `virtiofs` REMOVED
  (breaks docker-desktop bootstrap).
- `guiApplications=false` (2026-11-27): no Linux GUI apps in use — drops the
  WSLg stack (weston/RDP/pulseaudio), ~100-300MB permanent savings. Takes
  effect on next WSL VM restart. Backup `.wslconfig.bak-20261127-gui`;
  revert if any x11 lane ever breaks.

### Pagefile lane + the gotcha

- Host pagefile sat on C: at ~28.7GB allocated (peak real usage only ~2.2GB).
  Move to D: armed via `devin-scripts\pagefile-explicit.ps1` — takes effect
  on reboot after the user clicks the UAC consent.
- **Gotcha**: `'D:\pagefile.sys 0 0'` (system-managed size) **silently falls
  back to C:** at boot — Windows will not system-manage a pagefile on a
  non-boot volume. The reliable form is explicit sizes:
  `'D:\pagefile.sys 8192 32768'` (registry `HKLM\SYSTEM\CurrentControlSet\
  Control\Session Manager\Memory Management\PagingFiles`, MultiString).
  `Win32_PageFileSetting` WMI is empty even when configured — read the
  registry, not WMI.

### 2026-11-26 crash-chain resolution (reference incident)

Chain: RAM pressure -> C: near-zero (0.4GB) -> Docker Desktop cannot write ->
engine dies -> data-disk stuck `device or resource busy` on next bootstrap.
Fixes landed: ~35GB docker prune + DMR dead-model removal + IDE caches (C:
0.4GB -> ~37-50GB free), Intel DSA broken stack removed (32 errors/day),
pagefile move armed, DockerEngineRescue installed. Zero crashes since the
00:30 reboot. Win11 system-file integrity verified clean (SFC/DISM, zero
corruption) — the instability was never Windows itself; residual crash risk
is the Docker Desktop 4.100.1 x Insider 26H2 (build 26340) interaction,
absorbed by the rescue watchdog until either the RAM upgrade or leaving
the Insider channel.
