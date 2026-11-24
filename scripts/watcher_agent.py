#!/usr/bin/env python3
"""SocialAuto watcher-agent — host-side execution satellite.

Polls the platform for due host-agent watchers, executes them under a
timeout, and posts results back. The platform (ops_watchers registry)
is the scheduler; this script only executes what it is told to run.

Env:
  SOCIAL_API_URL            default http://127.0.0.1:8083
  OPS_WATCHER_AGENT_TOKEN   shared secret (required)
  WATCHER_POLL_SECONDS      default 30
  WATCHER_MAX_PARALLEL      default 2

Run: python3 scripts/watcher_agent.py [--once] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

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
POLL = int(os.environ.get("WATCHER_POLL_SECONDS", "30"))
MAX_PAR = int(os.environ.get("WATCHER_MAX_PARALLEL", "2"))
TIMEOUT_CAP = 1800


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


def run_one(w: dict) -> None:
    name = w["name"]
    cmd = w.get("command") or ""
    timeout = min(int(w.get("timeout_seconds") or 300), TIMEOUT_CAP)
    print(f"[watcher-agent] run {name}: {cmd[:120]}", flush=True)
    started = time.time()
    try:
        p = subprocess.run(
            ["bash", "-lc", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.path.expanduser("~/cu130-slim"),
        )
        code, out = p.returncode, (p.stdout + p.stderr)[-1800:]
    except subprocess.TimeoutExpired:
        code, out = 124, f"TIMEOUT after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        code, out = 1, f"spawn error: {exc}"
    dur = round(time.time() - started, 1)
    try:
        _req(
            "POST",
            f"/ops/watchers/{name}/result",
            {
                "exit_code": code,
                "output_tail": out[-1800:],
                "duration_seconds": dur,
                "observed": False,
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[watcher-agent] result post failed for {name}: {exc}", flush=True)
    print(f"[watcher-agent] {name} -> exit {code} ({dur}s)", flush=True)


def observe_external(w: dict) -> None:
    """External watchers: report freshness from meta.log_path mtime.
    A missing `>>`-redirect log means the cron has never produced output —
    a real staleness signal for wildcard schedules, but expected for dated
    one-shot crons (specific dom/month fields) that haven't fired yet."""
    meta = w.get("meta") or {}
    log = meta.get("log_path")
    if not log:
        return
    path = os.path.expanduser(log)
    if not os.path.exists(path):
        sched = (w.get("schedule_cron") or "").split()
        dated = len(sched) >= 5 and any(
            f not in ("*", "?") for f in sched[2:5]
        )
        if dated:
            return
        _req(
            "POST",
            f"/ops/watchers/{w['name']}/result",
            {
                "exit_code": 2,
                "output_tail": f"log missing: {path} — cron has never written",
                "duration_seconds": 0,
                "observed": True,
            },
        )
        return
    try:
        age = time.time() - os.path.getmtime(path)
        stale_after = max(int(w.get("interval_seconds") or 300) * 4, 3600)
        _req(
            "POST",
            f"/ops/watchers/{w['name']}/result",
            {
                "exit_code": 0 if age <= stale_after else 2,
                "output_tail": f"log mtime age {int(age)}s (stale_after {stale_after}s)",
                "duration_seconds": 0,
                "observed": True,
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[watcher-agent] observe {w['name']} failed: {exc}", flush=True)


def tick(executor: ThreadPoolExecutor) -> None:
    try:
        payload = _req("GET", "/ops/watchers/due")
    except urllib.error.HTTPError as exc:
        print(
            f"[watcher-agent] due poll HTTP {exc.code}: {exc.read()[:200]!r}",
            flush=True,
        )
        return
    except Exception as exc:  # noqa: BLE001
        print(f"[watcher-agent] due poll failed: {exc}", flush=True)
        return
    for w in payload.get("observe", []):
        observe_external(w)
    for w in payload.get("due", []):
        if w.get("executor") == "host-agent":
            executor.submit(run_one, w)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not TOKEN:
        print("OPS_WATCHER_AGENT_TOKEN not set", file=sys.stderr)
        return 2
    if args.dry_run:
        print(_req("GET", "/ops/watchers/due"))
        return 0
    with ThreadPoolExecutor(max_workers=MAX_PAR) as ex:
        while True:
            tick(ex)
            if args.once:
                time.sleep(2)  # let submitted runs finish reporting
                return 0
            time.sleep(POLL)


if __name__ == "__main__":
    raise SystemExit(main())
