#!/usr/bin/env python3
"""redis_drift_guard.py — detect + heal redis container config drift.

The incident class (2026-11-25): the redis container predates the compose
`--requirepass` wiring and survives on restart policy alone — every mere
restart resurrects the ORIGINAL (passwordless) config, breaking every client
that authenticates (workers: "AUTH called without any password configured").
Only a compose recreate applies the durable command.

`--once` lane (heal_supervisor / cron):
  inspect redis → if `--requirepass` missing from the container command,
  recreate from the deploy compose project (never `up -d` with deps).
  exit 0 = healthy or healed; 1 = heal failed.
"""
import argparse
import json
import subprocess
import sys

COMPOSE_DIR = "/home/tbaltzakis/cu130-slim/deploy/socialauto-standalone"


def _run(cmd, cwd=None, timeout=120):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)


def inspect_cmd():
    r = _run(["docker", "inspect", "redis", "--format", "{{json .Config.Cmd}}"])
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout.strip()) or []
    except json.JSONDecodeError:
        return []


def recreate():
    r = _run(
        ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "redis"],
        cwd=COMPOSE_DIR,
        timeout=300,
    )
    return r.returncode == 0


def once() -> int:
    cmd = inspect_cmd()
    if cmd is None:
        print("redis container missing — nothing to guard")
        return 0
    if "--requirepass" in cmd:
        print("redis requirepass present — healthy")
        return 0
    print("redis requirepass MISSING — recreating from compose")
    if not recreate():
        print("recreate failed")
        return 1
    cmd = inspect_cmd() or []
    ok = "--requirepass" in cmd
    print("healed" if ok else "still drifted after recreate")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    if args.once:
        return once()
    ap.error("only --once is supported")
    return 2


if __name__ == "__main__":
    sys.exit(main())
