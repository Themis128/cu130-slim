#!/usr/bin/env python3
"""redis_drift_guard.py — detect + heal redis container config drift.

The incident class (2026-11-25): the redis container predates the compose
`--requirepass` wiring and survives on restart policy alone — every mere
restart resurrects the ORIGINAL (passwordless) config, breaking every client
that authenticates (workers: "AUTH called without any password configured").
Only a compose recreate applies the durable command.

Run modes:
  no args   — daemon loop (heal_supervisor spawns it persistent): heal check
              every 5 min. Respects the deploy lock (DEPLOY_LOCK env, fresh
              locks pause healing per the AGENTS.md safe-autonomy contract).
  --once    — single check (manual/cron). exit 0 = healthy or healed.

Paths: inside stack-watchers the repo is /app (ro) and the compose project
lives at /app/deploy/socialauto-standalone; on the WSL host it is
/home/tbaltzakis/cu130-slim/deploy/socialauto-standalone. Override with
REDIS_GUARD_COMPOSE_DIR.
"""
import argparse
import json
import os
import subprocess
import sys
import time

_CONTAINER_DIR = "/app/deploy/socialauto-standalone"
_HOST_DIR = "/home/tbaltzakis/cu130-slim/deploy/socialauto-standalone"
COMPOSE_DIR = os.environ.get(
    "REDIS_GUARD_COMPOSE_DIR",
    _CONTAINER_DIR if os.path.isdir(_CONTAINER_DIR) else _HOST_DIR,
)
DEPLOY_LOCK = os.environ.get("DEPLOY_LOCK", "/app/.deploy.lock")
DEPLOY_LOCK_MAX_AGE = int(os.environ.get("DEPLOY_LOCK_MAX_AGE", "3600"))
LOOP_SECONDS = int(os.environ.get("REDIS_GUARD_LOOP_SECONDS", "300"))


def deploy_locked() -> bool:
    try:
        return (time.time() - os.path.getmtime(DEPLOY_LOCK)) < DEPLOY_LOCK_MAX_AGE
    except OSError:
        return False


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
    cmd = ["docker", "compose"]
    for env_file in (os.path.join(COMPOSE_DIR, ".env"), "/app/.env"):
        if os.path.isfile(env_file):
            cmd += ["--env-file", env_file]
            break
    cmd += ["up", "-d", "--no-deps", "--force-recreate", "redis"]
    r = _run(cmd, cwd=COMPOSE_DIR, timeout=300)
    if r.returncode != 0:
        print("recreate failed: %s" % (r.stderr or "").strip()[:200])
    return r.returncode == 0


def check() -> int:
    cmd = inspect_cmd()
    if cmd is None:
        print("redis container missing — nothing to guard")
        return 0
    if "--requirepass" in cmd:
        print("redis requirepass present — healthy")
        return 0
    if deploy_locked():
        print("redis drifted but deploy lock fresh — skipping heal")
        return 0
    print("redis requirepass MISSING — recreating from compose")
    if not recreate():
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
        return check()
    while True:
        try:
            check()
        except Exception as exc:
            print("guard error: %s" % exc)
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
