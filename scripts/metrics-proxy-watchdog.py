#!/usr/bin/env python3
"""Metrics port-proxy watchdog.

Docker Desktop restarts sometimes break the Windows port-proxy for
192.168.1.23:9390 -> social-metrics:80: TCP accepts, HTTP resets, the
container stays "healthy", and Prometheus reports up{job="socialauto"}=0.
A container restart re-registers the forward. This script probes the
published endpoint and restarts social-metrics only when the proxy path
is dead — never resurrects a deliberately stopped container.

Scheduled via Windows Task Scheduler:
  wsl.exe -d Ubuntu-26.04 -e python3 /home/tbaltzakis/cu130-slim/scripts/metrics-proxy-watchdog.py"""

import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "http://localhost:9390/metrics"
CONTAINER = "social-metrics"
LOG = Path.home() / ".cache/metrics-proxy-watchdog.log"
LOG.parent.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    with open(LOG, "a") as f:
        f.write(f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}\n")


def probe(timeout: int) -> bool:
    try:
        urllib.request.urlopen(URL, timeout=timeout)
        return True
    except Exception:
        return False


# healthy path — nothing to do
if probe(8):
    sys.exit(0)

# only act if the container exists and is running
r = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}",
                    CONTAINER], capture_output=True, text=True)
state = r.stdout.strip() if r.returncode == 0 else "missing"
if state != "running":
    log(f"probe failed but {CONTAINER} is '{state}' — leaving alone")
    sys.exit(0)

log(f"probe failed while {CONTAINER} running — port-proxy dead, restarting")
r = subprocess.run(["docker", "restart", CONTAINER],
                   capture_output=True, text=True)
with open(LOG, "a") as f:
    f.write(r.stdout + r.stderr)
time.sleep(8)
if probe(10):
    log(f"recovered: {URL} answering after restart")
else:
    log("STILL BROKEN after restart — needs manual check")

# keep the log small
lines = LOG.read_text().splitlines()[-200:]
LOG.write_text("\n".join(lines) + "\n")
