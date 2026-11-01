#!/usr/bin/env python3
"""stack_watchers.py — one supervisor running every gate-watch in-process:
cdp_watch (:9222 bind → LinkedIn/WhatsApp consents), li-vetting-watch
(mailbox → Telegram), wa_code_watch (Meta SMS cooldown → verify).
Runs as the stack-watchers compose service."""
import subprocess, sys, threading, time

WATCHES = {
    "cdp": ".devin/skills/cdp-watch-ops/scripts/cdp_watch.py",
    "vetting": ".devin/skills/li-vetting-watch/scripts/vetting_watch.py",
}
for name, script in WATCHES.items():
    threading.Thread(
        target=lambda s=script: subprocess.run([sys.executable, s]),
        daemon=True).start()
    print(f"{name} watch running")
while True:
    time.sleep(3600)
