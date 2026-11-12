---
name: host-gates-ops
description: >-
  Consolidated diagnostics for the remaining Windows/WSL host-level gates —
  RAM pressure behind the vsock flaps, Docker Desktop / docker-desktop WSL
  distro health, the ChromeDebug scheduled task state, and the IDE
  wsl+docker-desktop remote-authority install-spam loop. One command prints
  live state + the exact remediation; --alert sends a deduped TG #ops digest
  when anything is degraded. Tool: scripts/host_gates_check.py.
---

# Host gates ops

The leftovers that live on the Windows host rather than in any repo.
`host_gates_check.py` probes all of them read-only:

| Gate | Check | Automation boundary |
|---|---|---|
| Host RAM | `/proc/meminfo` MemAvailable + Windows-side free via interop | alert-only — closing apps is the fix |
| vsock / docker-desktop distro | `docker info` probe + `wsl.exe -l -v` state | `wsl-vsock-autoheal.sh` + resume-heal tasks own repair |
| ChromeDebug task | `schtasks /query /tn ChromeDebug` via powershell interop | needs owner elevation to edit — report only |
| IDE remote authority | can't be probed — documented manual fix | close `wsl+docker-desktop` authority in the IDE, use `wsl+Ubuntu-24.04` |
| Docker Desktop process | `Docker Desktop.exe` presence via interop | watchdog owns relaunch |

## Usage

```bash
python3 scripts/host_gates_check.py            # status table
python3 scripts/host_gates_check.py --json     # machine-readable
python3 scripts/host_gates_check.py --alert    # TG #ops digest on degradation (deduped 12h)
```

Cron: `10 8 * * * python3 host_gates_check.py --alert` (daily digest lane).

## What it deliberately does NOT do

- No `wsl --shutdown`, no task edits, no process kills — those belong to
  `wsl-vsock-autoheal.sh`, `wsl-resume-heal.ps1`, and the watchdogs which
  already own restart authority. This script observes and reports so the
  human gates stay visible instead of silently flapping.
