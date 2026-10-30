#!/usr/bin/env python3
"""celery_probe.py — e2e Celery delivery verifier for SocialAuto.

Commands:
    probe <task.name> [--args '["a"]']  — send task, poll result + inspect
    topology                             — queues each worker consumes + llen
    drift                                — .env vs container env diff
    mount-check <path>                   — host vs container file hash

Runs on host (uses docker exec for worker/redis access). Exit 1 on failure.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CID_SENTINEL = "9f98e2ce-24da-46f3-a19f-8d7e0e875270"  # sent survey campaign


def sh(cmd: list[str], timeout: int = 60) -> str:
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, cwd=ROOT).stdout


def dc(*args: str, timeout: int = 60) -> str:
    return sh(["docker", "compose", "exec", "-T", *args], timeout)


def env_from_file() -> dict[str, str]:
    out = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def redis_pw() -> str:
    url = env_from_file().get("REDIS_URL", "")
    m = re.search(r"redis://:([^@]+)@", url)
    return m.group(1) if m else ""


def rcli(*args: str) -> str:
    return sh(["docker", "exec", "redis", "redis-cli", "-a", redis_pw(),
               "--no-auth-warning", *args])


def cmd_probe(task: str, args_json: str) -> int:
    # send the task from inside social-api, capture id
    code = (
        "from app.worker.celery_app import celery_app\n"
        f"r = celery_app.send_task('{task}', args={args_json})\n"
        "print(r.id)\n"
    )
    tid = dc("social-api", "python", "-c", code).strip().splitlines()[-1].strip()
    print(f"sent task {task} id={tid}")
    ok = False
    for i in range(12):
        time.sleep(5)
        st = dc("social-api", "python", "-c",
                "from app.worker.celery_app import celery_app\n"
                "from celery.result import AsyncResult\n"
                f"print(AsyncResult('{tid}', app=celery_app).state)"
                ).strip()
        print(f"  t+{(i+1)*5}s state={st}")
        if st not in {"PENDING", "RECEIVED", "STARTED", "RETRY"}:
            ok = st == "SUCCESS"
            break
    if not ok:
        print("FAIL: task never consumed — matches the send_task anomaly")
        return 1
    print("delivered e2e")
    return 0


def cmd_topology() -> int:
    out = dc("social-worker-default", "celery", "-A", "app.worker.celery_app",
             "inspect", "active_queues")
    print(out)
    for q in ("default", "celery", "media", "publishing", "messenger"):
        n = rcli("llen", q).strip()
        print(f"llen {q}: {n or 'err'}")
    return 0


def cmd_drift() -> int:
    env = env_from_file()
    bad = 0
    for key in ("REDIS_URL", "DATABASE_URL", "N8N_API_KEY"):
        want = env.get(key, "")
        got = dc("social-api", "printenv", key).strip()
        if want and got != want:
            print(f"DRIFT {key}: .env != container (container stale — restart)")
            bad += 1
        else:
            print(f"ok {key}")
    return 1 if bad else 0


def cmd_mount_check(path: str) -> int:
    win = f"/mnt/c/Users/tbaltzakis/cu130-slim/{path}"
    hh = hashlib.sha256(Path(win).read_bytes()).hexdigest()[:12] if Path(win).exists() else "missing"
    ch = dc("social-api", "sha256sum", f"/app/app/{path.split('backend/app/')[-1]}",
            timeout=30).strip().split(" ")[0][:12]
    print(f"host({hh}) container({ch})")
    if hh != ch:
        print("STALE MOUNT — push file through: docker compose cp <file> social-api:/app/app/<rel>")
        return 1
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    cmd = args[0]
    if cmd == "probe" and len(args) >= 2:
        aix = args.index("--args") if "--args" in args else None
        return cmd_probe(args[1], args[aix + 1] if aix is not None else "[]")
    if cmd == "topology":
        return cmd_topology()
    if cmd == "drift":
        return cmd_drift()
    if cmd == "mount-check" and len(args) >= 2:
        return cmd_mount_check(args[1])
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())


# --- Shift.Forward adoption #1: SCOPE-style outcome check ---------------------
# A task can return succeeded while the result is plausible-but-wrong —
# e.g. survey_autosend returning {'sent': 0} when pending recipients exist.
# assert_result validates the result SHAPE + semantic content, not just
# the celery outcome.


def assert_result(task_name: str, result: dict, expect: dict) -> list[str]:
    """Verify a task result against expected semantic keys.

    expect: {"sent": (int, [1])} means key 'sent' must be int and in [1].
    Returns a list of violations — empty = outcome is semantically right.
    """
    bad = []
    for key, rule in expect.items():
        v = result.get(key) if isinstance(result, dict) else None
        typ, allowed = rule
        if not isinstance(v, typ):
            bad.append(f"{task_name}.{key}: {v!r} not {typ.__name__}")
        elif allowed and v not in allowed:
            bad.append(f"{task_name}.{key}: {v!r} not in {allowed}")
    return bad


# usage:
#   r = {"sent": 0}
#   print(assert_result("survey_autosend", r, {"sent": (int, [1])}))
