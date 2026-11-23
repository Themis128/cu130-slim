#!/usr/bin/env python3
"""Watcher inventory — scan every watchdog/healer tier and upsert registry rows.

Sources:
  1. WSL crontab        → tier=host, executor=external (legacy still runs them)
  2. heal_supervisor.py WATCHES registry → tier=host, executor=host-agent candidates
  3. Celery beat_schedule (via `docker exec social-api`) → tier=platform, executor=celery
  4. docker compose ps healthchecks → tier=compose, executor=external

Run on the WSL host:  python3 scripts/watcher_inventory.py [--dry-run] [--apply]
Default is --dry-run; --apply POSTs to /ops/watchers/sync with the agent token.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

API = os.environ.get("SOCIAL_API_URL", "http://127.0.0.1:8083").rstrip("/")
TOKEN = os.environ.get("OPS_WATCHER_AGENT_TOKEN", "")
REPO = os.path.expanduser("~/cu130-slim")
if not TOKEN:
    # cron-friendly fallback: pull the shared secret from the repo .env
    try:
        for _line in open(os.path.join(REPO, ".env")):
            if _line.startswith("OPS_WATCHER_AGENT_TOKEN="):
                TOKEN = _line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    except OSError:
        pass

CRON_SKIP = re.compile(r"^\s*#|^\s*$")


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80] or "unnamed"


def scan_crontab() -> list[dict]:
    try:
        out = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return []
    items = []
    for line in out.splitlines():
        if CRON_SKIP.match(line):
            continue
        # cron line: m h dom mon dow command...
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        sched, cmd = " ".join(parts[:5]), parts[5].strip()
        # find script path for a stable name
        m = re.search(r"([\w./~-]+\.(?:py|sh))", cmd)
        name_src = m.group(1) if m else cmd[:60]
        name = "cron-" + _slug(os.path.basename(name_src))
        interval = _cron_interval(parts[:5])
        items.append(
            {
                "name": name,
                "title": f"cron: {os.path.basename(name_src)}",
                "tier": "host",
                "kind": "watch",
                "executor": "external",
                "managed_by": "external",
                "command": cmd,
                "schedule_cron": sched,
                "interval_seconds": interval,
                "timeout_seconds": 600,
                "enabled": True,
                "meta": {"source": "crontab", "log_path": _log_hint(cmd)},
            }
        )
    return items


def _cron_interval(fields: list[str]) -> int:
    """Rough cadence estimate in seconds for staleness checks."""
    m, h, dom, mon, dow = fields
    if m.startswith("*/"):
        return int(m[2:]) * 60
    if h == "*" and m.isdigit():
        return 3600
    if dom != "*" or mon != "*" or dow != "*":
        return 86400
    return 3600  # hourly-ish default


def _log_hint(cmd: str) -> str:
    m = re.search(r">>?\s*(\S+\.(?:log|out|txt))", cmd)
    return m.group(1) if m else ""


def scan_supervisor() -> list[dict]:
    """heal_supervisor.py WATCHES — candidates for host-agent migration."""
    sup = os.path.join(REPO, "heal_supervisor.py")
    if not os.path.exists(sup):
        return []
    try:
        src = open(sup, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    # entries: "name": {"kind": "watch", "script": "path.py", "tier": "host"}
    items = []
    for m in re.finditer(
        r'["\']([\w-]+)["\']\s*:\s*\{[^}]*"kind"\s*:\s*"(\w+)"[^}]*"script"\s*:\s*"([^"]+)"',
        src,
    ):
        name, kind, script = m.group(1), m.group(2), m.group(3)
        items.append(
            {
                "name": f"sup-{_slug(name)}",
                "title": f"heal_supervisor: {name}",
                "tier": "host",
                "kind": kind
                if kind in ("watch", "heal", "sync", "report", "janitor")
                else "watch",
                "executor": "host-agent",
                "managed_by": "external",  # pilot flips to platform after verify
                "command": f"python3 {script} --once",
                "interval_seconds": 300,
                "timeout_seconds": 900,
                "enabled": True,
                "meta": {"source": "heal_supervisor", "script": script},
            }
        )
    return items


def scan_celery() -> list[dict]:
    """Beat schedule via docker exec into social-api (has app deps)."""
    script = (
        "import json;from app.worker.celery_app import celery_app;"
        "print(json.dumps({k:{'task':v.get('task'),'schedule':str(v.get('schedule'))}"
        " for k,v in celery_app.conf.beat_schedule.items()}))"
    )
    try:
        p = subprocess.run(
            ["docker", "exec", "social-api", "python", "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        sched = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception as exc:
        print(f"[inventory] celery beat scan failed: {exc}", file=sys.stderr)
        return []
    items = []
    for key, v in sched.items():
        task = v.get("task") or ""
        interval = 300
        sm = re.search(r"(\d+)", str(v.get("schedule", "")))
        if sm:
            interval = max(int(sm.group(1)) * 60, 60)  # crontab minute field approx
        items.append(
            {
                "name": "celery-" + _slug(key),
                "title": f"beat: {key}",
                "tier": "platform",
                "kind": "watch",
                "executor": "celery",
                "managed_by": "platform",
                "command": task,
                "schedule_cron": str(v.get("schedule", "")),
                "interval_seconds": interval,
                "timeout_seconds": 900,
                "enabled": True,
                "meta": {"source": "celery_beat", "beat_key": key},
            }
        )
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", choices=["cron", "supervisor", "celery"], default=None)
    args = ap.parse_args()

    items = []
    if not args.only or args.only == "cron":
        items += scan_crontab()
    if not args.only or args.only == "supervisor":
        items += scan_supervisor()
    if not args.only or args.only == "celery":
        items += scan_celery()

    # dedupe by name
    seen, uniq = set(), []
    for it in items:
        if it["name"] in seen:
            continue
        seen.add(it["name"])
        uniq.append(it)
    print(f"[inventory] {len(uniq)} watchers found")
    for it in uniq:
        print(
            f"  {it['tier']:>8} {it['executor']:<11} {it['managed_by']:<8} {it['name']}"
        )

    if not args.apply:
        print("[inventory] dry-run — pass --apply to sync")
        return 0
    if not TOKEN:
        print("OPS_WATCHER_AGENT_TOKEN not set", file=sys.stderr)
        return 2
    req = urllib.request.Request(
        f"{API}/api/v1/ops/watchers/sync",
        data=json.dumps({"watchers": uniq}).encode(),
        method="POST",
        headers={"X-Service-Token": TOKEN, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        print("[inventory] sync:", r.read().decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
