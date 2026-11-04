#!/usr/bin/env python3
"""Rescue stack-ops (the idle-sleep wake proxy) when it dies.

stack-ops is the DNS/proxy entrypoint for every managed sidecar
(linkedin/fb/tiktok sidecars, comfyui, warp-proxy, ...). When it exits,
workers hit ``[Errno -2] Name or service not known`` on every sidecar URL —
silent publish/DM failures until someone notices (observed 2026-11-03).

The compose watchdog already retries restarts, but it hands off after its
backoff budget — and a host-port squatter blocks every retry the same way.
This script is the escalation layer:

  1. If stack-ops is running → exit quietly.
  2. If Exited → `docker compose up -d stack-ops`.
  3. If start fails on a port bind → identify the squatter process
     (PID + command line) and send a Slack alert. It does NOT kill foreign
     processes — that's a human call.

Schedule every 10 min alongside compose-watchdog:

  schtasks /create /tn "cu130-stack-ops-rescue" /sc minute /mo 10 ^
    /tr "pythonw C:\\Users\\tbaltzakis\\cu130-slim\\scripts\\stack_ops_rescue.py"
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "logs", "stack-ops-rescue.log")
COMPOSE_CMD = ["docker", "compose", "up", "-d", "stack-ops"]


def log(msg: str) -> None:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg)


def docker(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=REPO)


def stack_ops_state() -> str:
    r = docker(["docker", "inspect", "stack-ops", "--format", "{{.State.Status}}"])
    return r.stdout.strip() if r.returncode == 0 else "missing"


def squatter_for_port(port: int) -> str:
    """Return 'PID cmdline' of whatever holds the port, else a best-effort note."""
    try:
        ps = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "$c = Get-NetTCPConnection -LocalPort "
                + str(port)
                + " -ErrorAction SilentlyContinue | Select-Object -First 1;"
                + "if ($c) { $p = Get-Process -Id $c.OwningProcess; "
                + "$w = Get-CimInstance Win32_Process -Filter \"ProcessId=$($c.OwningProcess)\"; "
                + "Write-Output ($c.OwningProcess.ToString() + ' ' + $p.ProcessName + ' | ' + $w.CommandLine) }",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        out = ps.stdout.strip()
        return out or "squatter process not identified"
    except Exception as exc:
        return f"squatter lookup failed: {exc}"


def slack_alert(text: str) -> None:
    url = os.environ.get("SLACK_ALERTS_WEBHOOK_URL")
    if not url:
        # Try the repo .env without printing secrets
        env_path = os.path.join(REPO, "social-automation", "backend", ".env")
        if os.path.exists(env_path):
            for line in open(env_path, encoding="utf-8", errors="replace"):
                if line.startswith("SLACK_ALERTS_WEBHOOK_URL="):
                    url = line.split("=", 1)[1].strip().strip('"')
    if not url:
        log("no SLACK_ALERTS_WEBHOOK_URL — alert not sent")
        return
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        log("slack alert sent")
    except Exception as exc:
        log(f"slack alert failed: {exc}")


def main() -> int:
    state = stack_ops_state()
    if state == "running":
        return 0

    log(f"stack-ops state={state} — attempting restart")
    r = docker(COMPOSE_CMD, timeout=120)
    if stack_ops_state() == "running":
        log("stack-ops recovered")
        slack_alert(":white_check_mark: stack-ops was down and has been restarted by the rescue script.")
        return 0

    # Still down — look for a port squatter in the error output
    err = (r.stderr or "") + (r.stdout or "")
    port = None
    m = re.search(r"127\.0\.0\.1:(\d+)", err)
    if m:
        port = int(m.group(1))
    squatter = squatter_for_port(port) if port else "no bind-conflict port found in compose output"
    msg = (
        f":rotating_light: stack-ops is DOWN and cannot restart.\n"
        f"state={state} | squatter on :{port}: `{squatter}`\n"
        f"compose error tail: {err.strip()[-300:]}"
    )
    log(msg)
    slack_alert(msg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
