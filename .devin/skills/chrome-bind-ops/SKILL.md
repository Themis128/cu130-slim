---
name: chrome-bind-ops
description: Bind :9222 CDP despite broken WSL->Windows interop — three
  fallback launchers that all land in the user's INTERACTIVE session (the
  only session that can bind user-session CDP): a schtasks /IT interactive
  task, a Windows-side self-watching launcher .bat, and a one-time
  registry Run-key. Tools: scripts/bind_9222.py — `status`, `schedule`,
  `install-runkey`, `install-watcher`.
---
# Chrome :9222 bind — interactive-session launchers
