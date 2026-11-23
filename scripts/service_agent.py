#!/usr/bin/env python3
"""SocialAuto service-agent — status reporter + safe action executor.

Two jobs per cycle:
  1. Collect live status for every registry service (docker ps, systemctl,
     n8n API, Windows tasks) and POST /ops/services/report.
  2. Poll /ops/services/actions and execute pending allowlisted actions
     (docker restart/start/stop, systemctl restart, windows task run/stop),
     then POST /ops/services/{name}/action-result.

Env: SOCIAL_API_URL, OPS_WATCHER_AGENT_TOKEN, SERVICE_POLL_SECONDS (default 30),
     N8N_API_KEY/N8N_HOST optional (read from .env if unset).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

API = os.environ.get("SOCIAL_API_URL", "http://127.0.0.1:8083").rstrip("/")
TOKEN = os.environ.get("OPS_WATCHER_AGENT_TOKEN", "")
POLL = int(os.environ.get("SERVICE_POLL_SECONDS", "30"))
REPO = os.path.expanduser("~/cu130-slim")
TIMEOUT = 120


def _req(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}/api/v1{path}",
        data=data,
        method=method,
        headers={"X-Service-Token": TOKEN, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _env(key: str) -> str:
    try:
        for line in open(
            os.path.join(REPO, ".env"), encoding="utf-8", errors="replace"
        ):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return os.environ.get(key, "")


def collect() -> list[dict]:
    reports = []
    # docker
    try:
        out = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.State}}|{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
        for line in out.splitlines():
            p = line.split("|")
            if len(p) >= 3:
                name, state, status = p[0], p[1], "|".join(p[2:])
                health = (
                    "healthy"
                    if "healthy" in status.lower()
                    else ("unhealthy" if "unhealthy" in status.lower() else "unknown")
                )
                reports.append(
                    {
                        "name": "docker-"
                        + _slug(name.rsplit("-", 1)[0] if name[-1].isdigit() else name),
                        "status": state,
                        "health": health,
                        "detail": status[:200],
                    }
                )
                reports.append(
                    {
                        "name": "docker-" + _slug(name),
                        "status": state,
                        "health": health,
                        "detail": status[:200],
                    }
                )
    except Exception as exc:
        print(f"[service-agent] docker collect failed: {exc}", flush=True)
    # systemd user units
    try:
        out = subprocess.run(
            [
                "systemctl",
                "--user",
                "list-units",
                "--type=service",
                "--all",
                "--no-pager",
                "--no-legend",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
        for line in out.splitlines():
            p = line.split()
            if len(p) >= 4 and p[0].endswith(".service"):
                reports.append(
                    {
                        "name": "systemd-" + _slug(p[0]),
                        "status": p[2].lower(),
                        "health": "ok" if p[2] == "active" else "degraded",
                        "detail": f"{p[2]}/{p[3]}",
                    }
                )
    except Exception:
        pass
    return reports


def _slug(s: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:120] or "unnamed"


def run_action(svc: dict) -> tuple[bool, str]:
    action = svc.get("pending_action") or ""
    executor = svc.get("executor")
    target = svc.get("target") or ""
    try:
        if executor == "docker-cli":
            if action == "restart":
                cmd = ["docker", "restart", target]
            elif action == "start":
                cmd = ["docker", "start", target]
            elif action == "stop":
                cmd = ["docker", "stop", target]
            elif action == "logs":
                cmd = ["docker", "logs", "--tail", "50", target]
            elif action == "status":
                cmd = [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.State.Status}} {{.State.Health.Status}}",
                    target,
                ]
            else:
                return False, f"unsupported action {action}"
        elif executor == "systemctl":
            if action == "restart":
                cmd = ["systemctl", "--user", "restart", target]
            elif action == "status":
                cmd = ["systemctl", "--user", "is-active", target]
            else:
                return False, f"unsupported action {action} for systemctl"
        elif executor == "windows-task":
            ps = {
                "status": f"(Get-ScheduledTask -TaskName '{target}').State",
                "restart": f"Stop-ScheduledTask -TaskName '{target}'; Start-ScheduledTask -TaskName '{target}'",
            }.get(action)
            if not ps:
                return False, f"unsupported action {action} for windows-task"
            cmd = ["powershell.exe", "-NoProfile", "-Command", ps]
        else:
            return False, f"executor {executor} has no action runner"
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        return p.returncode == 0, (p.stdout + p.stderr)[-1500:]
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT after {TIMEOUT}s"
    except Exception as exc:  # noqa: BLE001
        return False, f"spawn error: {exc}"


def cycle() -> None:
    # 1. status reports
    try:
        reports = collect()
        if reports:
            _req(
                "POST",
                "/ops/services/report",
                {"reports": reports, "reported_by": "service-agent"},
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[service-agent] report failed: {exc}", flush=True)
    # 2. pending actions
    try:
        pending = _req("GET", "/ops/services/actions").get("actions", [])
    except Exception as exc:  # noqa: BLE001
        print(f"[service-agent] actions poll failed: {exc}", flush=True)
        return
    for svc in pending:
        if svc.get("pending_action") in ("", None):
            continue
        print(
            f"[service-agent] action {svc['pending_action']} on {svc['name']}",
            flush=True,
        )
        ok, out = run_action(svc)
        try:
            _req(
                "POST",
                f"/ops/services/{svc['name']}/action-result",
                {"ok": ok, "output_tail": out},
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[service-agent] result post failed: {exc}", flush=True)
        print(f"[service-agent] {svc['name']} -> {'ok' if ok else 'FAIL'}", flush=True)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--collect-only", action="store_true")
    args = ap.parse_args()
    if not TOKEN:
        print("OPS_WATCHER_AGENT_TOKEN not set", file=sys.stderr)
        return 2
    while True:
        cycle()
        if args.once:
            return 0
        time.sleep(POLL)


if __name__ == "__main__":
    raise SystemExit(main())
