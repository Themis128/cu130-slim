#!/usr/bin/env python3
"""leftover_sweep — daily cleanup of the recurring repair leftovers:

1. Stray Created-state containers (hash-prefixed recreate-race leftovers) -> rm
2. Posts stuck 'publishing' > 45 min with no open queue row -> TG alert
3. Suppressed posts backlog count -> informational line (media_repair handles)

Usage: leftover_sweep.py [--apply]   (dry-run reports only without --apply)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

DOCKER = "docker.exe"
PG = ["exec", "-i", "social-postgres", "psql", "-U", "social_user",
      "-d", "social_automation", "-tA", "-c"]


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([DOCKER, *args], capture_output=True, text=True, timeout=60)


def psql(sql: str) -> str:
    r = docker(*PG, sql)
    return r.stdout.strip()


def tg(text: str) -> None:
    script = os.path.expanduser(
        "~/cu130-slim/.devin/skills/telegram-alert-ops/scripts/tg_alert.py")
    if os.path.exists(script):
        subprocess.run(["python3", script, "ops", text],
                       capture_output=True, timeout=60)


def stray_containers(apply: bool) -> list[str]:
    r = docker("ps", "-a", "--filter", "status=created",
               "--format", "{{.Names}} {{.CreatedAt}}")
    names = [l.split()[0] for l in r.stdout.splitlines() if l.strip()]
    removed = []
    for n in names:
        if apply:
            docker("rm", "-f", n)
        removed.append(n)
    return removed


def stuck_publishing() -> list[str]:
    return [l for l in psql(
        "SELECT id FROM posts WHERE status='publishing' AND updated_at < now()-interval '45 minutes'"
        " AND NOT EXISTS (SELECT 1 FROM publish_queue q WHERE q.post_id=posts.id AND q.status IN ('pending','processing'))"
    ).splitlines() if l]


def suppressed_count() -> str:
    return psql("SELECT count(*) FROM posts WHERE status='suppressed'")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    strays = stray_containers(args.apply)
    stuck = stuck_publishing()
    supp = suppressed_count()

    print(f"stray Created containers: {len(strays)}"
          + (f" removed={strays}" if strays else ""))
    print(f"stuck publishing>45m (no open queue row): {len(stuck)}"
          + (f" {stuck}" if stuck else ""))
    print(f"suppressed posts backlog: {supp}")

    if strays or stuck:
        tg("leftover-sweep: strays=" + ",".join(strays or ["-"])
           + " stuck_publishing=" + ",".join(stuck or ["-"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
