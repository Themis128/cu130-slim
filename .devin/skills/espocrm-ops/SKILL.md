---
name: espocrm-ops
description: EspoCRM k3s ops — diagnose the espocrm/ espocrm-daemon pods.
  The failure class: stale PVC app-files (35-line daemon.php/command.php
  stubs) that call v10-dropped commands (config:set) → daemon CrashLoop.
  Tool: scripts/espo_check.py — reports container state + stub detection +
  the reseed path.
---
# EspoCRM ops
Detects stale-PVC daemon crashes (config:set on a stub) + the reseed path.
