---
name: win-interop-quiet
description: >-
  Eliminate CMD/PowerShell console-flash popups caused by WSL→Windows interop
  spawns (powershell.exe, *.exe under /mnt/c, wsl.exe, adb.exe, docker.exe).
  Every such call makes Windows allocate a visible console; cron scripts that
  do it unconditionally every pass produced ~500 flashes/day (2026-11-16).
  Use when popups appear, when adding/modifying cron or watchdog scripts that
  touch Windows binaries, or when auditing spawn cadence. Companion to
  windows-host-ops (host tasks) and docker-engine-ops (engine pinning).
---

# Win Interop Quiet — no console flashes from WSL cron

## The rule

**Never invoke a Windows binary unconditionally on a cron/watch path.** Each
spawn = one visible console window on the user's desktop. Budget: aim for
**≤1 interop spawn per script per hour**, and zero on healthy steady-state
passes.

## The five patterns (all proven on this host)

1. **Batch probes into ONE spawn.** `host_gates_check.py win_batch()` collects
   RAM + schtasks + process + `wsl -l -v` in a single `powershell.exe` call
   returning JSON. Replaced 3–4 spawns per */10min pass. Gotcha: `win()` runs
   through bash double-quotes — escape every `$` as `\$` or bash eats it.
2. **Stamp-throttle secondary probes.** `docker_engine_guard.py` probes
   `docker.exe` at most hourly (`exe-check.stamp`); `cdp-debug-watch.py`
   batches its reap+dialog janitor into one hourly powershell call
   (`cdp-debug-watch.janitor` stamp). Cheap local checks keep their cadence.
3. **Share state across scripts.** `host_gates_check` writes
   `~/.local/state/host-gates-ops/host-ram-available-mb`; `fb_session_heal`
   reads it (<15min fresh) instead of spawning powershell for RAM every */30.
   If another script already measures a Windows value, persist it and reuse.
4. **Prefer the WSL-native binary; .exe only as fallback.** Verified-name
   pattern for docker: use bare `docker` only when `docker info --format
   '{{.Name}}'` returns `docker-desktop`, else the pinned `docker.exe`.
   Same for adb: Linux `adb` first (sees the phone over wireless ADB at
   100.84.31.85:5555), `adb.exe` fallback only.
5. **Headless Windows scheduled task for Windows-side work.**
   `McpToolkit-WinCheck` runs `conhost --headless … wsl.exe … --win-only` —
   verification happens inside a hidden console, not a flashing one.

## Audit tool

```bash
python3 .devin/skills/win-interop-quiet/scripts/win_spawn_audit.py          # all cron scripts
python3 .devin/skills/win-interop-quiet/scripts/win_spawn_audit.py --json   # machine-readable
python3 .devin/skills/win-interop-quiet/scripts/win_spawn_audit.py --paths ~/scripts ~/home-ops/scripts
```

Classifies each interop reference HOT (unconditional per-pass — fix),
GUARD (rare remediation branch — acceptable), REVIEW. Ignores path constants,
candidate lists, docstrings, and comments. Residual HOT hits are usually
deliberate remediation spawns (e.g. `wsl.exe --shutdown` in
`wsl-idle-shutdown.sh`) — verify before "fixing".

## Known-good references

- `cu130-slim/.devin/skills/host-gates-ops/scripts/host_gates_check.py` — `win_batch()`
- `cu130-slim/.devin/skills/docker-engine-ops/scripts/docker_engine_guard.py` — hourly exe stamp
- `~/scripts/cdp-debug-watch.py` — hourly batched janitor
- `~/scripts/phone-adb-watch.sh`, `phone_fb_approve.py`, `x_phone_bridge.py` — Linux-adb-only
- `home-ops/scripts/mcp-toolkit-heal.sh` — WSL-local cheap checks + `--win-only` lane
- `cu130-slim/.devin/skills/fb-reauth-ops/scripts/fb_session_heal.py` — shared RAM file

## When adding a new cron script

1. Run `win_spawn_audit.py --paths <new-script>` — must show 0 HOT.
2. If it needs Windows data: batch it, stamp-throttle it, or read a shared
   state file — never spawn per pass.
3. Remediation spawns (heal/escalate/restart) are fine — they fire rarely.
