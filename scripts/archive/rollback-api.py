#!/usr/bin/env python3
"""Instant rollback: point api-gateway back at the previous API slot.
Works while the previous slot container still exists (running or stopped —
stopped slots are restarted first).
Usage: rollback-api.py"""

import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACTIVE_CONF = ROOT / "deploy/nginx/upstream/active.conf"
GATEWAY = "social-api-gateway"

m = re.search(r"social-api(-green)?", ACTIVE_CONF.read_text())
cur = m.group(0) if m else "social-api"
if cur == "social-api":
    prev, compose = "social-api-green", ["docker", "compose", "--profile", "bluegreen"]
else:
    prev, compose = "social-api", ["docker", "compose"]
prev_port = 8083 if prev == "social-api" else 18083

print(f"==> live: {cur} — rolling back to {prev}")

r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", prev],
                   capture_output=True, text=True)
if "true" not in r.stdout:
    print(f"==> {prev} not running — starting it")
    subprocess.run([*compose, "up", "-d", prev], cwd=ROOT, check=True)


def healthy() -> bool:
    try:
        urllib.request.urlopen(f"http://localhost:{prev_port}/health",
                               timeout=5)
        return True
    except Exception:
        return False


for _ in range(40):
    if healthy():
        break
    time.sleep(3)
if not healthy():
    print(f"!! {prev} unhealthy — abort", file=sys.stderr)
    sys.exit(1)

ACTIVE_CONF.write_text(f"server {prev}:8000 max_fails=2 fail_timeout=10s;\n")
subprocess.run(["docker", "exec", GATEWAY, "nginx", "-t"], check=True,
               capture_output=True)
subprocess.run(["docker", "exec", GATEWAY, "nginx", "-s", "reload"], check=True)
time.sleep(1)
r = subprocess.run(["docker", "exec", GATEWAY, "wget", "-qO-",
                    "http://127.0.0.1:8080/health"], capture_output=True)
if r.returncode == 0:
    print(f"==> rollback complete — traffic on {prev}")
else:
    print("!! gateway verify failed", file=sys.stderr)
    sys.exit(1)
