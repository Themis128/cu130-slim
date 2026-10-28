#!/usr/bin/env python3
"""Zero-downtime deploy for social-api (blue/green behind api-gateway).

Flow:  detect live slot -> start the other slot (runs `alembic upgrade head`
       on boot — keep migrations expand-only) -> wait for /health -> rewrite
       gateway upstream -> nginx -s reload -> verify through gateway -> the old
       slot keeps running as instant rollback until you stop it.

Usage:
  scripts/deploy-api.py            # deploy current working tree to idle slot
  scripts/deploy-api.py --stop-old # also stop the old slot after cutover"""

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACTIVE_CONF = ROOT / "deploy/nginx/upstream/active.conf"
GATEWAY = "social-api-gateway"
STOP_OLD = "--stop-old" in sys.argv[1:]


def active_slot() -> str:
    import re
    m = re.search(r"social-api(-green)?", ACTIVE_CONF.read_text())
    return m.group(0) if m else "social-api"


def slot_port(slot: str) -> int:
    return 8083 if slot == "social-api" else 18083


def healthy(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://localhost:{port}/health", timeout=5)
        return True
    except Exception:
        return False


def wait_healthy(port: int, tries: int = 60) -> bool:
    for _ in range(tries):
        if healthy(port):
            return True
        time.sleep(3)
    return False


live = active_slot()
if live == "social-api":
    new, compose = "social-api-green", ["docker", "compose", "--profile", "bluegreen"]
else:
    new, compose = "social-api", ["docker", "compose"]

print(f"==> live slot: {live} — deploying to {new}")
print(f"==> starting {new} (bind-mount picks up current working tree; "
      "alembic runs on boot)")
subprocess.run([*compose, "up", "-d", new], cwd=ROOT, check=True)

print(f"==> waiting for {new} /health on :{slot_port(new)}")
if not wait_healthy(slot_port(new)):
    print(f"!! {new} never became healthy — aborting, traffic still on {live}",
          file=sys.stderr)
    sys.exit(1)

print(f"==> switching gateway upstream to {new}")
ACTIVE_CONF.write_text(f"server {new}:8000 max_fails=2 fail_timeout=10s;\n")
subprocess.run(["docker", "exec", GATEWAY, "nginx", "-t"], check=True,
               capture_output=True)
subprocess.run(["docker", "exec", GATEWAY, "nginx", "-s", "reload"], check=True)
time.sleep(1)

print("==> verifying traffic through gateway")
if not healthy(slot_port(new)):
    sys.exit(1)
r = subprocess.run(["docker", "exec", GATEWAY, "wget", "-qO-",
                    "http://127.0.0.1:8080/health"], capture_output=True)
if r.returncode == 0:
    print(f"==> gateway serving from {new} — cutover complete")
else:
    print(f"!! gateway check failed — rolling back to {live}", file=sys.stderr)
    ACTIVE_CONF.write_text(f"server {live}:8000 max_fails=2 fail_timeout=10s;\n")
    subprocess.run(["docker", "exec", GATEWAY, "nginx", "-s", "reload"])
    sys.exit(1)

if STOP_OLD:
    print(f"==> stopping old slot {live} (90s drain)")
    subprocess.run(["docker", "stop", "-t", "90", live], capture_output=True)
    print(f"==> {live} stopped")
else:
    print(f"==> {live} left running as instant rollback: "
          "scripts/rollback-api.py")
