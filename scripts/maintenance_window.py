#!/usr/bin/env python3
"""maintenance_window.py — hold the shared browser bridge for manual ops.

The browser-novnc bridge (:9223) is shared by pollers (celery-beat tasks)
and every platform that needs browser automation. Manual CM/profile edits
get hijacked by rotating pollers — this tool pauses the poller fleet so a
manual session stays stable, then restores it.

Usage:
  maintenance_window.py start [minutes]   # pause pollers
  maintenance_window.py stop              # unpause everything
  maintenance_window.py status            # show paused containers + bridge owner

What it pauses: celery-beat (stops new scheduled tasks) plus the workers
that run browser-touching tasks (publishing, default, messenger, media).
social-api stays up so API/UI keep working."""

import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKERS = ["celery-beat", "social-worker-publishing", "social-worker-default",
           "social-worker-messenger", "social-worker-media"]

cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

if cmd == "start":
    print(f"Pausing poller fleet: {' '.join(WORKERS)}")
    subprocess.run(["docker", "compose", "pause", *WORKERS],
                   cwd=ROOT, check=True)
    print()
    print("Poller fleet paused. The bridge may still hold a busy-owner "
          "briefly —")
    print("check 'status' and use force:true on /session/start if it "
          "won't release.")
    print(f"Run '{sys.argv[0]} stop' when finished.")
elif cmd == "stop":
    print("Resuming poller fleet")
    subprocess.run(["docker", "compose", "unpause", *WORKERS],
                   cwd=ROOT, check=True)
    print("Done — verify with: docker compose ps")
elif cmd == "status":
    print("── paused containers ──")
    r = subprocess.run(["docker", "ps", "--filter", "status=paused",
                        "--format", "{{.Names}}"],
                       capture_output=True, text=True)
    for name in sorted(r.stdout.splitlines()):
        print(name)
    print("── bridge session ──")
    try:
        print(urllib.request.urlopen("http://localhost:9223/session/status",
                                     timeout=5).read().decode())
    except Exception:
        print("bridge unreachable")
    print()
else:
    print(f"usage: {sys.argv[0]} {{start|stop|status}}", file=sys.stderr)
    sys.exit(2)
