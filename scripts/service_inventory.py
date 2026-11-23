#!/usr/bin/env python3
"""Service inventory — scan every manageable service tier and upsert registry rows.

Sources:
  1. `docker ps -a` (+compose service labels)  → tier=docker, executor=docker-cli
  2. `systemctl --user list-units`             → tier=systemd, executor=systemctl
  3. n8n REST /workflows (N8N_API_KEY)         → tier=n8n, executor=n8n-api
  4. `powershell.exe Get-ScheduledTask`        → tier=windows, executor=windows-task

Critical infra is seeded with allowed_actions=["status","logs"] — visible
but not remotely restartable without a policy change.

Run on WSL host:  python3 scripts/service_inventory.py [--apply] [--only docker|systemd|n8n|windows]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request

API = os.environ.get("SOCIAL_API_URL", "http://127.0.0.1:8083").rstrip("/")
TOKEN = os.environ.get("OPS_WATCHER_AGENT_TOKEN", "")
REPO = os.path.expanduser("~/cu130-slim")

# services the platform may NEVER restart remotely — observe + logs only
CRITICAL = {
    "social-postgres",
    "redis",
    "social-redis",
    "n8n",
    "n8n-task-runner",
    "social-cloudflared",
    "cloudflared",
    "browser-novnc",
    "browser-bridge",
    "social-api",
    "social-worker",
    "comfyui",
    "chroma",
    "chroma-rescue",
    "engagement-browser",
    "social-media-comfyui-gpu",
    "docker-socket-proxy",
}
OBSERVE_ONLY_ACTIONS = ["status", "logs"]
MANAGE_ACTIONS = ["status", "logs", "restart", "start", "stop"]


def _slug(s: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:120] or "unnamed"


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


def scan_docker() -> list[dict]:
    try:
        out = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--format",
                '{{.Names}}|{{.Status}}|{{.Label "com.docker.compose.service"}}',
            ],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
    except Exception as exc:
        print(f"[inventory] docker scan failed: {exc}", file=sys.stderr)
        return []
    items = []
    _seen_names = set()
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < 2:
            continue
        name, status, compose_svc = (
            parts[0],
            parts[1],
            (parts[2] if len(parts) > 2 else ""),
        )
        critical = name in CRITICAL or compose_svc in CRITICAL
        svc_name = compose_svc or name
        row_name = "docker-" + _slug(svc_name)
        if row_name in _seen_names:
            row_name = "docker-" + _slug(name)
        _seen_names.add(row_name)
        items.append(
            {
                "name": row_name,
                "title": f"container: {name}",
                "tier": "docker",
                "kind": "container",
                "executor": "docker-cli",
                "managed_by": "platform",
                "target": name,
                "meta": {
                    "compose_service": compose_svc,
                    "docker_status": status,
                    "allowed_actions": OBSERVE_ONLY_ACTIONS
                    if critical
                    else MANAGE_ACTIONS,
                    "critical": critical,
                },
                "enabled": True,
            }
        )
    return items


def scan_systemd() -> list[dict]:
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
    except Exception:
        return []
    items = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        unit, active, sub = parts[0], parts[2], parts[3]
        if not unit.endswith(".service"):
            continue
        items.append(
            {
                "name": "systemd-" + _slug(unit),
                "title": f"systemd: {unit}",
                "tier": "systemd",
                "kind": "service",
                "executor": "systemctl",
                "managed_by": "external",
                "target": unit,
                "meta": {"active": active, "sub": sub, "allowed_actions": ["status"]},
                "enabled": active == "active",
            }
        )
    return items


def scan_n8n() -> list[dict]:
    """n8n workflows via `docker exec n8n n8n list:workflow` (live JWT in DB)."""
    try:
        out = subprocess.run(
            ["docker", "exec", "n8n", "n8n", "list:workflow"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception as exc:
        print(f"[inventory] n8n scan failed: {exc}", file=sys.stderr)
        return []
    items = []
    for line in out.splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        wid, name = line.split("|", 1)
        items.append({
            "name": "n8n-" + _slug(name),
            "title": f"n8n workflow: {name.strip()}",
            "tier": "n8n", "kind": "workflow", "executor": "n8n-api",
            "managed_by": "external", "target": wid.strip(),
            "meta": {"workflow_active": True, "allowed_actions": ["status"]},
            "enabled": True,
        })
    return items


def scan_windows() -> list[dict]:
    ps = (
        "Get-ScheduledTask | Where-Object {$_.TaskPath -notlike '\\Microsoft\\*' -and $_.State -ne 'Disabled'} | "
        'ForEach-Object { "$($_.TaskName)|$($_.State)" }'
    )
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    except Exception as exc:
        print(f"[inventory] windows scan failed: {exc}", file=sys.stderr)
        return []
    items = []
    for line in out.splitlines():
        line = line.strip().strip("\ufeff")
        if "|" not in line:
            continue
        name, state = line.rsplit("|", 1)
        name = name.strip().strip('"')
        if not name:
            continue
        items.append(
            {
                "name": "win-" + _slug(name),
                "title": f"windows task: {name}",
                "tier": "windows",
                "kind": "task",
                "executor": "windows-task",
                "managed_by": "external",
                "target": name,
                "meta": {"task_state": state.strip(), "allowed_actions": ["status"]},
                "enabled": True,
            }
        )
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--only", choices=["docker", "systemd", "n8n", "windows"], default=None
    )
    args = ap.parse_args()

    items = []
    for src, fn in [
        ("docker", scan_docker),
        ("systemd", scan_systemd),
        ("n8n", scan_n8n),
        ("windows", scan_windows),
    ]:
        if not args.only or args.only == src:
            got = fn()
            print(f"[inventory] {src}: {len(got)}")
            items += got

    seen, uniq = set(), []
    for it in items:
        if it["name"] in seen:
            continue
        seen.add(it["name"])
        uniq.append(it)
    print(f"[inventory] {len(uniq)} services total")
    if not args.apply:
        print("[inventory] dry-run — pass --apply to sync")
        return 0
    if not TOKEN:
        print("OPS_WATCHER_AGENT_TOKEN not set", file=sys.stderr)
        return 2
    req = urllib.request.Request(
        f"{API}/api/v1/ops/services/sync",
        data=json.dumps({"services": uniq}).encode(),
        method="POST",
        headers={"X-Service-Token": TOKEN, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        print("[inventory] sync:", r.read().decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
